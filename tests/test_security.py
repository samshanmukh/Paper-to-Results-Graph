import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app.security import (
    FixedWindowRateLimiter,
    reject_oversized_request,
    require_api_key,
)


def request(*, host: str = "203.0.113.8", headers: dict[str, str] | None = None) -> Request:
    raw_headers = [
        (name.lower().encode(), value.encode())
        for name, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/run/example-m1",
            "headers": raw_headers,
            "client": (host, 43210),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


class ApiKeyTests(unittest.TestCase):
    def test_remote_access_is_default_deny(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(HTTPException) as caught:
                require_api_key(request())
        self.assertEqual(caught.exception.status_code, 503)

    def test_valid_bearer_key_is_accepted(self):
        key = "a" * 32
        with patch.dict(os.environ, {"VERIGRAPH_API_KEY": key}, clear=True):
            require_api_key(request(headers={"Authorization": f"Bearer {key}"}))

    def test_invalid_key_is_rejected(self):
        with patch.dict(os.environ, {"VERIGRAPH_API_KEY": "a" * 32}, clear=True):
            with self.assertRaises(HTTPException) as caught:
                require_api_key(request(headers={"Authorization": "Bearer wrong"}))
        self.assertEqual(caught.exception.status_code, 401)

    def test_loopback_bypass_must_be_explicit_and_direct(self):
        env = {"VERIGRAPH_ALLOW_LOCAL_MUTATIONS": "true"}
        with patch.dict(os.environ, env, clear=True):
            require_api_key(request(host="127.0.0.1", headers={"Host": "localhost:8787"}))
            with self.assertRaises(HTTPException):
                require_api_key(
                    request(
                        host="127.0.0.1",
                        headers={"Host": "localhost:8787", "X-Forwarded-For": "203.0.113.8"},
                    )
                )

    def test_loopback_bypass_rejects_host_rebinding(self):
        env = {"VERIGRAPH_ALLOW_LOCAL_MUTATIONS": "true"}
        hostile_hosts = ("evil.example", "localhost.evil.example", "localhost@evil.example")
        with patch.dict(os.environ, env, clear=True):
            for host in hostile_hosts:
                with self.subTest(host=host), self.assertRaises(HTTPException):
                    require_api_key(request(host="127.0.0.1", headers={"Host": host}))

    def test_loopback_bypass_rejects_cross_site_browser_requests(self):
        env = {"VERIGRAPH_ALLOW_LOCAL_MUTATIONS": "true"}
        hostile_headers = (
            {
                "Host": "localhost:8787",
                "Origin": "https://evil.example",
                "Sec-Fetch-Site": "cross-site",
            },
            {
                "Host": "localhost:8787",
                "Origin": "http://127.0.0.1:8787",
                "Sec-Fetch-Site": "same-site",
            },
            {
                "Host": "localhost:8787",
                "Origin": "http://localhost:8787",
                "Sec-Fetch-Site": "cross-site",
            },
        )
        with patch.dict(os.environ, env, clear=True):
            for headers in hostile_headers:
                with self.subTest(headers=headers), self.assertRaises(HTTPException):
                    require_api_key(request(host="::1", headers=headers))

    def test_loopback_bypass_accepts_same_origin_browser_request(self):
        env = {"VERIGRAPH_ALLOW_LOCAL_MUTATIONS": "true"}
        headers = {
            "Host": "localhost:8787",
            "Origin": "http://localhost:8787",
            "Sec-Fetch-Site": "same-origin",
        }
        with patch.dict(os.environ, env, clear=True):
            require_api_key(request(host="::1", headers=headers))


class RateLimiterTests(unittest.TestCase):
    def test_fixed_window_rejects_and_resets(self):
        limiter = FixedWindowRateLimiter(window_seconds=60)
        self.assertEqual(limiter.check("client", "run", 2, now=10), 1)
        self.assertEqual(limiter.check("client", "run", 2, now=11), 0)
        with self.assertRaises(HTTPException) as caught:
            limiter.check("client", "run", 2, now=12)
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(limiter.check("client", "run", 2, now=71), 1)


class RequestBoundsTests(unittest.TestCase):
    def test_oversized_request_is_rejected(self):
        env = {"VERIGRAPH_MAX_REQUEST_BYTES": "1024"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(HTTPException) as caught:
                reject_oversized_request(request(headers={"Content-Length": "1025"}))
        self.assertEqual(caught.exception.status_code, 413)


if __name__ == "__main__":
    unittest.main()
