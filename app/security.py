"""Authentication, request bounds, and in-process rate limiting for the API."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


_PIPELINE_RUN_PATH = re.compile(r"^/api/run/[a-z0-9]+(?:-[a-z0-9]+)*$")


def _truthy(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(name: str, default: int, *, minimum: int = 1, maximum: int = 100_000) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return min(maximum, max(minimum, value))


def _direct_client_host(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _parse_authority(value: str, scheme: str) -> tuple[str, int] | None:
    """Return a normalized HTTP authority, rejecting ambiguous syntax."""
    if (
        not value
        or value != value.strip()
        or any(char in value for char in "\x00\r\n/\\?#@,")
    ):
        return None
    try:
        parsed = urlsplit(f"//{value}")
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if not hostname or parsed.username is not None or parsed.password is not None:
        return None
    if scheme not in {"http", "https"}:
        return None
    return hostname.lower(), port or (443 if scheme == "https" else 80)


def _is_loopback_authority(authority: tuple[str, int] | None) -> bool:
    if authority is None:
        return False
    hostname, _ = authority
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _is_same_local_origin(request: Request, host: tuple[str, int]) -> bool:
    """Reject cross-site browser traffic while allowing direct local clients."""
    fetch_sites = request.headers.getlist("sec-fetch-site")
    if len(fetch_sites) > 1:
        return False
    if fetch_sites and fetch_sites[0].strip().lower() not in {"same-origin", "none"}:
        return False

    origins = request.headers.getlist("origin")
    if not origins:
        return True
    if len(origins) != 1:
        return False
    try:
        origin = urlsplit(origins[0].strip())
    except ValueError:
        return False
    if (
        origin.scheme not in {"http", "https"}
        or origin.scheme != request.url.scheme
        or not origin.netloc
        or origin.path
        or origin.query
        or origin.fragment
    ):
        return False
    origin_authority = _parse_authority(origin.netloc, origin.scheme)
    return _is_loopback_authority(origin_authority) and origin_authority == host


def _is_direct_loopback(request: Request) -> bool:
    """Allow bypass only for a direct, same-origin loopback request."""
    if any(
        request.headers.get(header)
        for header in ("forwarded", "x-forwarded-for", "cf-connecting-ip", "x-real-ip")
    ):
        return False
    try:
        if not ipaddress.ip_address(_direct_client_host(request)).is_loopback:
            return False
    except ValueError:
        return False

    hosts = request.headers.getlist("host")
    if len(hosts) != 1:
        return False
    host = _parse_authority(hosts[0], request.url.scheme)
    return _is_loopback_authority(host) and _is_same_local_origin(request, host)


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer":
        return token.strip()
    return request.headers.get("x-verigraph-api-key", "").strip()


def _authorized_rocketride_executor(request: Request) -> bool:
    """Allow the local executor without exposing a credential to its LLM."""
    if not _truthy("VERIGRAPH_ENABLE_ROCKETRIDE_EXECUTOR"):
        return False
    if request.method != "POST" or not _PIPELINE_RUN_PATH.fullmatch(request.url.path):
        return False
    if request.headers.get("origin") or request.headers.get("referer") or any(
        name.lower().startswith("sec-fetch-") for name in request.headers.keys()
    ):
        return False
    return _is_direct_loopback(request)


def require_api_key(request: Request) -> None:
    """Authorize an expensive or mutating request.

    Remote access is default-deny. Local bypass must be explicitly enabled and
    is accepted only for a direct loopback connection without proxy headers.
    """
    if _authorized_rocketride_executor(request):
        return
    if _truthy("VERIGRAPH_ALLOW_LOCAL_MUTATIONS") and _is_direct_loopback(request):
        return

    expected = os.environ.get("VERIGRAPH_API_KEY", "").strip()
    if len(expected) < 32:
        raise HTTPException(
            503,
            "Protected API access is not configured. Set VERIGRAPH_API_KEY to at least 32 characters.",
        )
    supplied = _bearer_token(request)
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})


@dataclass
class _Window:
    started: float
    count: int


class FixedWindowRateLimiter:
    """Small dependency-free limiter suitable for a single API process.

    Multi-instance deployments should enforce the same limits at the gateway.
    """

    def __init__(self, window_seconds: int = 60):
        self.window_seconds = window_seconds
        self._windows: dict[tuple[str, str], _Window] = {}
        self._lock = threading.Lock()

    def check(self, identity: str, bucket: str, limit: int, *, now: float | None = None) -> int:
        current = time.monotonic() if now is None else now
        key = (identity, bucket)
        with self._lock:
            if key not in self._windows and len(self._windows) >= 10_000:
                cutoff = current - self.window_seconds
                self._windows = {
                    existing_key: window
                    for existing_key, window in self._windows.items()
                    if window.started > cutoff
                }
                if len(self._windows) >= 10_000:
                    oldest = min(self._windows, key=lambda item: self._windows[item].started)
                    self._windows.pop(oldest, None)
            item = self._windows.get(key)
            if item is None or current - item.started >= self.window_seconds:
                item = _Window(started=current, count=0)
                self._windows[key] = item
            if item.count >= limit:
                retry_after = max(1, int(self.window_seconds - (current - item.started)) + 1)
                raise HTTPException(
                    429,
                    "Rate limit exceeded.",
                    headers={"Retry-After": str(retry_after)},
                )
            item.count += 1
            return max(0, limit - item.count)

    def clear(self) -> None:
        with self._lock:
            self._windows.clear()


RATE_LIMITER = FixedWindowRateLimiter()


def client_identity(request: Request) -> str:
    """Use the authenticated key fingerprint or the direct peer address."""
    token = _bearer_token(request)
    if token:
        fingerprint = hashlib.sha256(token.encode()).hexdigest()[:16]
        return f"key:{fingerprint}"
    return f"ip:{_direct_client_host(request)}"


def enforce_rate_limit(request: Request, bucket: str, default_limit: int) -> int:
    env_name = f"VERIGRAPH_RATE_LIMIT_{bucket.upper()}"
    limit = _positive_int(env_name, default_limit)
    return RATE_LIMITER.check(client_identity(request), bucket, limit)


def authorize_expensive_request(
    request: Request,
    *,
    bucket: str = "mutation",
    default_limit: int = 20,
) -> None:
    require_api_key(request)
    enforce_rate_limit(request, bucket, default_limit)


def max_request_bytes() -> int:
    return _positive_int(
        "VERIGRAPH_MAX_REQUEST_BYTES",
        26_000_000,
        minimum=1_024,
        maximum=100_000_000,
    )


def reject_oversized_request(request: Request) -> None:
    raw = request.headers.get("content-length")
    if not raw:
        return
    try:
        length = int(raw)
    except ValueError:
        raise HTTPException(400, "Invalid Content-Length header.")
    if length < 0:
        raise HTTPException(400, "Invalid Content-Length header.")
    if length > max_request_bytes():
        raise HTTPException(413, f"Request body exceeds the {max_request_bytes()} byte limit.")


class RequestBodyLimitMiddleware:
    """Bound the bytes received even when Content-Length is absent or false."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = max_request_bytes()
        body = bytearray()
        disconnected = False
        received = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
                break
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            received += len(chunk)
            if received > limit:
                response = JSONResponse(
                    {"detail": f"Request body exceeds the {limit} byte limit."},
                    status_code=413,
                )
                await response(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        pending: Message = (
            {"type": "http.disconnect"}
            if disconnected
            else {"type": "http.request", "body": bytes(body), "more_body": False}
        )
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return pending
            return await receive()

        await self.app(scope, replay, send)
