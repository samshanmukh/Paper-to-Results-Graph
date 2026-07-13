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
    max_bytes: int = 8_000_000,
) -> str:
    """Fetch bounded HTML from an official arXiv URL via Web Unlocker."""
    from app.arxiv import canonicalize_arxiv_url

    url = canonicalize_arxiv_url(url, kind="html")
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or not 1 <= max_bytes <= 25_000_000
    ):
        raise ValueError("max_bytes must be between 1 and 25000000")
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
        text = data
    elif isinstance(data, bytes):
        if len(data) > max_bytes:
            raise RuntimeError(f"Bright Data response exceeds {max_bytes} byte limit")
        text = data.decode("utf-8", errors="replace")
    if isinstance(data, dict):
        for key in ("raw_html", "html", "body", "content"):
            if key in data and data[key]:
                text = str(data[key])
                break
        else:
            raise RuntimeError(
                f"unexpected Bright Data dict response keys: {list(data.keys())[:8]}"
            )
    elif not isinstance(data, (str, bytes)):
        raise RuntimeError(f"unexpected Bright Data response type: {type(data).__name__}")

    if len(text.encode("utf-8")) > max_bytes:
        raise RuntimeError(f"Bright Data response exceeds {max_bytes} byte limit")
    return text
