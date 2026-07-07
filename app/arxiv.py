"""Fetch paper text from arXiv links (HTML or PDF).

Large PDFs can fail mid-download in some environments (IncompleteRead around
4 MB). We therefore:
  1. Use the HTML page when the user pasted an /html/ link.
  2. Try the PDF export for abs/pdf links (chunked read + retries).
  3. Fall back to the arXiv HTML page when PDF fetch/parse fails.
"""

from __future__ import annotations

import http.client
import io
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from pypdf import PdfReader

_USER_AGENT = "groundtruth/1.0 (hackathon demo)"
_ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?", re.I)


def parse_arxiv_id(url: str) -> tuple[str, str | None]:
    m = _ARXIV_ID.search(url.strip())
    if not m:
        raise ValueError("couldn't find an arXiv id in that link")
    return m.group(1), m.group(2)


def _html_urls(arxiv_id: str, version: str | None, original: str) -> list[str]:
    if "/html/" in original.lower():
        return [original.strip().split("#")[0].rstrip("/")]
    urls = []
    if version:
        urls.append(f"https://arxiv.org/html/{arxiv_id}{version}")
    urls.append(f"https://arxiv.org/html/{arxiv_id}")
    return urls


def _pdf_urls(arxiv_id: str) -> list[str]:
    return [
        f"https://export.arxiv.org/pdf/{arxiv_id}.pdf",
        f"https://arxiv.org/pdf/{arxiv_id}.pdf",
        f"https://export.arxiv.org/pdf/{arxiv_id}",
    ]


def html_to_text(html: str) -> str:
    from html.parser import HTMLParser

    class _Extractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts: list[str] = []
            self._skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "nav", "header", "footer"):
                self._skip += 1
            elif tag in ("p", "div", "section", "h1", "h2", "h3", "h4", "li", "br", "tr"):
                if not self._skip:
                    self.parts.append("\n")

        def handle_endtag(self, tag):
            if tag in ("script", "style", "nav", "header", "footer") and self._skip:
                self._skip -= 1

        def handle_data(self, data):
            if not self._skip:
                chunk = data.strip()
                if chunk:
                    self.parts.append(chunk + " ")

    parser = _Extractor()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return re.sub(r" +", " ", text).strip()


def _http_get(url: str, timeout: int = 120) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported URL scheme: {url}")
    ctx = ssl.create_default_context()
    conn = http.client.HTTPSConnection(parsed.hostname, timeout=timeout, context=ctx)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    conn.request("GET", path, headers={"User-Agent": _USER_AGENT})
    resp = conn.getresponse()
    if resp.status not in (200, 206):
        body = resp.read(300).decode(errors="replace")
        conn.close()
        raise RuntimeError(f"HTTP {resp.status} for {url}: {body}")
    chunks: list[bytes] = []
    while True:
        chunk = resp.read(65536)
        if not chunk:
            break
        chunks.append(chunk)
    conn.close()
    return b"".join(chunks)


def _fetch_bytes(url: str, retries: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return _http_get(url)
        except Exception as e:
            last = e
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"download failed after {retries} tries: {last}")


def _pdf_to_text(blob: bytes) -> str:
    reader = PdfReader(io.BytesIO(blob))
    return "\n".join(page.extract_text() or "" for page in reader.pages[:12])


def _fetch_html_text(urls: list[str]) -> str:
    last: Exception | None = None
    for url in urls:
        try:
            html = _fetch_bytes(url).decode("utf-8", errors="replace")
            text = html_to_text(html)
            if len(text.strip()) >= 200:
                return text
            last = ValueError(f"HTML too short from {url}")
        except Exception as e:
            last = e
    raise RuntimeError(f"HTML fetch failed: {last}")


def _fetch_pdf_text(urls: list[str]) -> str:
    last: Exception | None = None
    for url in urls:
        try:
            blob = _fetch_bytes(url)
            text = _pdf_to_text(blob)
            if len(text.strip()) >= 200:
                return text
            last = ValueError(f"PDF text too short from {url}")
        except Exception as e:
            last = e
    raise RuntimeError(f"PDF fetch failed: {last}")


def fetch_arxiv_text(url: str) -> str:
    arxiv_id, version = parse_arxiv_id(url)
    original = url.strip()

    if "/html/" in original.lower():
        return _fetch_html_text(_html_urls(arxiv_id, version, original))

    try:
        return _fetch_pdf_text(_pdf_urls(arxiv_id))
    except Exception:
        return _fetch_html_text(_html_urls(arxiv_id, version, original))
