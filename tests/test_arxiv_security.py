from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.arxiv import (
    _fetch_bytes,
    _html_urls,
    _http_get,
    canonicalize_arxiv_url,
    fetch_arxiv_text,
    parse_arxiv_id,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1705.08292", ("1705.08292", None)),
        ("arXiv:1705.08292", ("1705.08292", None)),
        ("1705.08292v3", ("1705.08292", "v3")),
        ("hep-th/9901001v2", ("hep-th/9901001", "v2")),
        ("https://arxiv.org/abs/1705.08292", ("1705.08292", None)),
        (
            "https://export.arxiv.org/pdf/1705.08292v2.pdf",
            ("1705.08292", "v2"),
        ),
        ("https://arxiv.org/html/math.GT/0309136", ("math.GT/0309136", None)),
    ],
)
def test_parse_arxiv_id_accepts_only_canonical_ids_and_urls(value, expected):
    assert parse_arxiv_id(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "junk-1705.08292",
        "1735.08292",
        "hep-th/9935001",
        "ARXIV:1705.08292",
        "foo../0309136",
        "../../1705.08292",
        "http://arxiv.org/abs/1705.08292",
        "ftp://arxiv.org/abs/1705.08292",
        "https://evil.test/abs/1705.08292",
        "https://arxiv.org.evil.test/abs/1705.08292",
        "https://arxiv.org@127.0.0.1/abs/1705.08292",
        "https://user:pass@arxiv.org/abs/1705.08292",
        "https://arxiv.org:443/abs/1705.08292",
        "https://arxiv.org/abs/1705.08292?download=1",
        "https://arxiv.org/abs/1705.08292#section",
        "https://arxiv.org/not-a-paper/1705.08292",
        "https://arxiv.org/abs/%31%37%30%35.08292",
    ],
)
def test_parse_arxiv_id_rejects_ssrf_and_ambiguous_inputs(value):
    with pytest.raises(ValueError):
        parse_arxiv_id(value)


def test_canonical_url_drops_input_authority_and_normalizes_pdf_suffix():
    assert canonicalize_arxiv_url(
        "https://EXPORT.ARXIV.ORG/pdf/1705.08292v2"
    ) == "https://arxiv.org/pdf/1705.08292v2.pdf"
    assert canonicalize_arxiv_url("1705.08292", kind="html") == (
        "https://arxiv.org/html/1705.08292"
    )


def test_html_fallback_never_reuses_the_original_url():
    assert _html_urls(
        "1705.08292", None, "https://attacker.test/html/1705.08292"
    ) == ["https://arxiv.org/html/1705.08292"]


def test_fetch_bytes_rejects_non_arxiv_target_before_network_or_retries():
    with patch("app.arxiv._http_get") as get, patch("app.arxiv.time.sleep") as sleep:
        with pytest.raises(ValueError):
            _fetch_bytes("https://127.0.0.1/html/1705.08292")
    get.assert_not_called()
    sleep.assert_not_called()


def _connection_with_response(body: bytes, content_length: str | None = None):
    response = MagicMock(status=200)
    response.getheader.return_value = content_length
    chunks = [body, b""]
    response.read.side_effect = lambda _size: chunks.pop(0)
    connection = MagicMock()
    connection.getresponse.return_value = response
    return connection


def test_http_get_enforces_stream_limit_without_content_length():
    connection = _connection_with_response(b"12345")
    with patch("app.arxiv.http.client.HTTPSConnection", return_value=connection):
        with pytest.raises(RuntimeError, match="byte limit"):
            _http_get("https://arxiv.org/html/1705.08292", max_bytes=4)
    connection.request.assert_called_once()
    connection.close.assert_called_once()


def test_http_get_rejects_oversized_declared_content_length_before_reading():
    connection = _connection_with_response(b"", content_length="500")
    with patch("app.arxiv.http.client.HTTPSConnection", return_value=connection):
        with pytest.raises(RuntimeError, match="byte limit"):
            _http_get("https://arxiv.org/html/1705.08292", max_bytes=100)
    connection.getresponse.return_value.read.assert_not_called()
    connection.close.assert_called_once()


def test_fetch_arxiv_text_reconstructs_html_target_from_parsed_reference():
    with patch("app.arxiv._fetch_html_text", return_value="paper " * 100) as fetch:
        text = fetch_arxiv_text("https://ARXIV.ORG/html/1705.08292v2")
    assert len(text) > 200
    fetch.assert_called_once_with([
        "https://arxiv.org/html/1705.08292v2",
        "https://arxiv.org/html/1705.08292",
    ])
