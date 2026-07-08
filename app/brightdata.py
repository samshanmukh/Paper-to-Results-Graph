"""Bright Data Web Unlocker fallback for paper URL fetches.

Used when direct urllib downloads fail (timeouts, bot blocks, IncompleteRead).
Requires BRIGHTDATA_API_TOKEN in the environment (optional — arxiv fetch still
works without it via direct HTTP + arXiv HTML fallback).
"""

from __future__ import annotations

import os


def is_configured() -> bool:
    if os.environ.get("BRIGHTDATA_DISABLED", "").lower() in ("1", "true", "yes"):
        return False
    return bool(_api_token())


def _api_token() -> str | None:
    return os.environ.get("BRIGHTDATA_API_TOKEN") or os.environ.get("BRIGHT_DATA_API_TOKEN")


def fetch_url_text(
    url: str,
    *,
    timeout: int = 120,
    poll_timeout: int = 180,
) -> str:
    """Fetch a URL body as text via Bright Data Web Unlocker."""
    token = _api_token()
    if not token:
        raise RuntimeError("BRIGHTDATA_API_TOKEN is not set")

    from brightdata import SyncBrightDataClient

    kwargs: dict = {
        "response_format": "raw",
        "timeout": timeout,
        "poll_timeout": poll_timeout,
    }
    zone = os.environ.get("BRIGHTDATA_WEB_UNLOCKER_ZONE")
    if zone:
        kwargs["zone"] = zone

    with SyncBrightDataClient(token=token, timeout=timeout) as client:
        result = client.scrape_url(url, **kwargs)

    if not result.success:
        raise RuntimeError(result.error or "Bright Data scrape failed")

    data = result.data
    if data is None:
        raise RuntimeError("Bright Data returned an empty body")
    if isinstance(data, str):
        return data
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    if isinstance(data, dict):
        for key in ("raw_html", "html", "body", "content"):
            if key in data and data[key]:
                return str(data[key])
        raise RuntimeError(f"unexpected Bright Data dict response keys: {list(data.keys())[:8]}")
    raise RuntimeError(f"unexpected Bright Data response type: {type(data).__name__}")
