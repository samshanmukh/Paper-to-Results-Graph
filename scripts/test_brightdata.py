#!/usr/bin/env python3
"""Unit tests for Bright Data paper-fetch fallback (no live API unless token set)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class BrightDataConfigTests(unittest.TestCase):
    def test_not_configured_without_token(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("BRIGHTDATA")}
        with patch.dict(os.environ, env, clear=True):
            from app import brightdata

            self.assertFalse(brightdata.is_configured())

    def test_configured_with_token(self):
        with patch.dict(os.environ, {"BRIGHTDATA_API_TOKEN": "test-token"}, clear=False):
            from app import brightdata

            self.assertTrue(brightdata.is_configured())

    def test_disabled_flag(self):
        with patch.dict(
            os.environ,
            {"BRIGHTDATA_API_TOKEN": "test-token", "BRIGHTDATA_DISABLED": "1"},
            clear=False,
        ):
            from app import brightdata

            self.assertFalse(brightdata.is_configured())


class BrightDataFetchTests(unittest.TestCase):
    def test_fetch_url_text_parses_string_body(self):
        mock_result = MagicMock(success=True, data="<html><body><p>Hello paper abstract</p></body></html>")
        mock_client = MagicMock()
        mock_client.scrape_url.return_value = mock_result
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch.dict(os.environ, {"BRIGHTDATA_API_TOKEN": "test-token"}, clear=False):
            with patch("brightdata.SyncBrightDataClient", return_value=mock_client):
                from app.brightdata import fetch_url_text

                html = fetch_url_text("https://arxiv.org/html/1705.08292")
        self.assertIn("Hello paper abstract", html)
        mock_client.scrape_url.assert_called_once()


class ArxivBrightDataFallbackTests(unittest.TestCase):
    def test_html_fallback_calls_brightdata_when_direct_fails(self):
        html = (
            "<html><body><p>"
            + ("Adaptive gradient methods generalize worse. " * 20)
            + "</p></body></html>"
        )

        with patch.dict(os.environ, {"BRIGHTDATA_API_TOKEN": "test-token"}, clear=False):
            with patch("app.arxiv._fetch_bytes", side_effect=RuntimeError("direct blocked")):
                with patch("app.brightdata.fetch_url_text", return_value=html) as bd:
                    from app.arxiv import _fetch_html_text

                    text = _fetch_html_text(["https://arxiv.org/html/1705.08292"])
        self.assertIn("Adaptive gradient", text)
        bd.assert_called_once()


if __name__ == "__main__":
    unittest.main()
