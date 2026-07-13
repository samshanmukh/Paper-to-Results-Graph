"""Safely fetch paper text from canonical arXiv identifiers and URLs."""

from __future__ import annotations

from dataclasses import dataclass
import http.client
import io
import re
import ssl
import time
import urllib.parse

from pypdf import PdfReader

_USER_AGENT = "verigraph/1.0 (research ingestion; contact via repository)"
_OFFICIAL_HOSTS = frozenset({"arxiv.org", "export.arxiv.org"})
_LEGACY_ARCHIVE = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?:\.[A-Z]{2})?"
_MODERN_ID = r"\d{2}(?:0[1-9]|1[0-2])\.\d{4,5}"
_LEGACY_ID = rf"{_LEGACY_ARCHIVE}/\d{{2}}(?:0[1-9]|1[0-2])\d{{3}}"
_ID_BODY = rf"(?:{_MODERN_ID}|{_LEGACY_ID})"
_FULL_ID = re.compile(rf"(?P<id>{_ID_BODY})(?P<version>v[1-9]\d*)?\Z")
_URL_PATH = re.compile(
    rf"/(?P<kind>abs|pdf|html)/(?P<id>{_ID_BODY})"
    rf"(?P<version>v[1-9]\d*)?(?P<suffix>\.pdf)?/?\Z",
)

MAX_HTML_BYTES = 8_000_000
MAX_PDF_BYTES = 25_000_000
MAX_EXTRACTED_TEXT_CHARS = 2_000_000


@dataclass(frozen=True)
class _ArxivReference:
    arxiv_id: str
    version: str | None
    kind: str | None
    host: str | None

    @property
    def full_id(self) -> str:
        return self.arxiv_id + (self.version or "")


def _parse_reference(value: str) -> _ArxivReference:
    if not isinstance(value, str):
        raise ValueError("arXiv input must be a string")
    value = value.strip()
    if not value:
        raise ValueError("arXiv input cannot be empty")

    bare_value = value.removeprefix("arXiv:")
    bare = _FULL_ID.fullmatch(bare_value)
    if bare:
        return _ArxivReference(
            bare.group("id"), bare.group("version"), None, None
        )

    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise ValueError("malformed arXiv URL") from exc
    if parsed.scheme.lower() != "https":
        raise ValueError("arXiv URLs must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("arXiv URLs cannot contain credentials")
    try:
        port = parsed.port
        host = (parsed.hostname or "").lower()
    except ValueError as exc:
        raise ValueError("arXiv URL has an invalid port") from exc
    if port is not None:
        raise ValueError("arXiv URLs cannot contain a custom port")
    if host not in _OFFICIAL_HOSTS:
        raise ValueError("arXiv URL host must be arxiv.org or export.arxiv.org")
    if parsed.query or parsed.fragment:
        raise ValueError("arXiv URLs cannot contain a query or fragment")

    match = _URL_PATH.fullmatch(parsed.path)
    if not match:
        raise ValueError("arXiv URL path must be /abs/<id>, /pdf/<id>, or /html/<id>")
    kind = match.group("kind").lower()
    suffix = match.group("suffix")
    if suffix and kind != "pdf":
        raise ValueError("the .pdf suffix is only valid for /pdf/ URLs")
    return _ArxivReference(
        match.group("id"),
        match.group("version").lower() if match.group("version") else None,
        kind,
        host,
    )


def parse_arxiv_id(value: str) -> tuple[str, str | None]:
    """Parse a bare canonical ID or an official HTTPS arXiv URL."""
    ref = _parse_reference(value)
    return ref.arxiv_id, ref.version


def canonicalize_arxiv_url(value: str, *, kind: str | None = None) -> str:
    """Return an arxiv.org URL reconstructed from validated input.

    The input hostname is deliberately not reused.  This makes the returned
    URL suitable for fetchers without carrying attacker-controlled authority,
    credentials, ports, queries, or fragments across the trust boundary.
    """
    ref = _parse_reference(value)
    target_kind = (kind or ref.kind or "abs").lower()
    if target_kind not in {"abs", "pdf", "html"}:
        raise ValueError("arXiv URL kind must be abs, pdf, or html")
    suffix = ".pdf" if target_kind == "pdf" else ""
    return f"https://arxiv.org/{target_kind}/{ref.full_id}{suffix}"


def _official_fetch_url(value: str) -> str:
    """Validate an internal fetch URL and preserve only its allowlisted host."""
    ref = _parse_reference(value)
    if ref.kind is None or ref.host is None:
        raise ValueError("internal arXiv fetch target must be an official HTTPS URL")
    suffix = ".pdf" if ref.kind == "pdf" else ""
    return f"https://{ref.host}/{ref.kind}/{ref.full_id}{suffix}"


def _html_urls(
    arxiv_id: str, version: str | None, original: str | None = None
) -> list[str]:
    # ``original`` remains in the signature for callers from earlier releases;
    # it is intentionally ignored so an input URL is never fetched verbatim.
    ref = _parse_reference(arxiv_id + (version or ""))
    urls = [f"https://arxiv.org/html/{ref.full_id}"]
    if ref.version:
        urls.append(f"https://arxiv.org/html/{ref.arxiv_id}")
    return urls


def _pdf_urls(arxiv_id: str, version: str | None = None) -> list[str]:
    ref = _parse_reference(arxiv_id + (version or ""))
    return [
        f"https://export.arxiv.org/pdf/{ref.full_id}.pdf",
        f"https://arxiv.org/pdf/{ref.full_id}.pdf",
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
    return re.sub(r" +", " ", text).strip()[:MAX_EXTRACTED_TEXT_CHARS]


def _http_get(
    url: str, timeout: int = 120, *, max_bytes: int = MAX_PDF_BYTES
) -> bytes:
    """Read a bounded response from an allowlisted arXiv HTTPS URL."""
    url = _official_fetch_url(url)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 300:
        raise ValueError("timeout must be between 1 and 300 seconds")
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or not 1 <= max_bytes <= MAX_PDF_BYTES
    ):
        raise ValueError(f"max_bytes must be between 1 and {MAX_PDF_BYTES}")

    parsed = urllib.parse.urlsplit(url)
    ctx = ssl.create_default_context()
    conn = http.client.HTTPSConnection(
        parsed.hostname, timeout=timeout, context=ctx
    )
    path = parsed.path or "/"
    try:
        conn.request(
            "GET",
            path,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept-Encoding": "identity",
            },
        )
        response = conn.getresponse()
        if response.status not in (200, 206):
            body = response.read(300).decode(errors="replace")
            raise RuntimeError(f"HTTP {response.status} for arXiv fetch: {body}")

        content_length = response.getheader("Content-Length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError as exc:
                raise RuntimeError("arXiv returned an invalid Content-Length") from exc
            if declared < 0 or declared > max_bytes:
                raise RuntimeError(f"arXiv response exceeds {max_bytes} byte limit")

        chunks: list[bytes] = []
        received = 0
        while True:
            chunk = response.read(min(65_536, max_bytes - received + 1))
            if not chunk:
                break
            received += len(chunk)
            if received > max_bytes:
                raise RuntimeError(f"arXiv response exceeds {max_bytes} byte limit")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        conn.close()


def _fetch_bytes(
    url: str, retries: int = 3, *, max_bytes: int = MAX_PDF_BYTES
) -> bytes:
    if isinstance(retries, bool) or not isinstance(retries, int) or not 1 <= retries <= 5:
        raise ValueError("retries must be between 1 and 5")
    # Validate before retrying so malformed/unsafe input never reaches a
    # network function and is not obscured as a transient download failure.
    url = _official_fetch_url(url)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return _http_get(url, max_bytes=max_bytes)
        except Exception as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"download failed after {retries} tries: {last}")


def _pdf_to_text(blob: bytes) -> str:
    if len(blob) > MAX_PDF_BYTES:
        raise ValueError(f"PDF exceeds {MAX_PDF_BYTES} byte limit")
    if not blob.lstrip().startswith(b"%PDF"):
        raise ValueError("arXiv PDF response is not a PDF")
    reader = PdfReader(io.BytesIO(blob))
    text = "\n".join(page.extract_text() or "" for page in reader.pages[:12])
    return text[:MAX_EXTRACTED_TEXT_CHARS]


def _fetch_html_text(urls: list[str]) -> str:
    last: Exception | None = None
    for candidate in urls:
        url = _official_fetch_url(candidate)
        if "/html/" not in urllib.parse.urlsplit(url).path.lower():
            raise ValueError("HTML fetch target must use an arXiv /html/ path")
        try:
            html = _fetch_bytes(url, max_bytes=MAX_HTML_BYTES).decode(
                "utf-8", errors="replace"
            )
            text = html_to_text(html)
            if len(text.strip()) >= 200:
                return text
            last = ValueError("arXiv HTML text is too short")
        except Exception as exc:
            last = exc
        if _brightdata_ready():
            try:
                from app.brightdata import fetch_url_text

                html = fetch_url_text(url, max_bytes=MAX_HTML_BYTES)
                text = html_to_text(html)
                if len(text.strip()) >= 200:
                    return text
                last = ValueError("Bright Data arXiv HTML text is too short")
            except Exception as exc:
                last = exc
    raise RuntimeError(f"HTML fetch failed: {last}")


def _brightdata_ready() -> bool:
    try:
        from app.brightdata import is_configured

        return is_configured()
    except Exception:
        return False


def _fetch_pdf_text(urls: list[str]) -> str:
    last: Exception | None = None
    for candidate in urls:
        url = _official_fetch_url(candidate)
        if "/pdf/" not in urllib.parse.urlsplit(url).path.lower():
            raise ValueError("PDF fetch target must use an arXiv /pdf/ path")
        try:
            blob = _fetch_bytes(url, max_bytes=MAX_PDF_BYTES)
            text = _pdf_to_text(blob)
            if len(text.strip()) >= 200:
                return text
            last = ValueError("arXiv PDF text is too short")
        except Exception as exc:
            last = exc
    raise RuntimeError(f"PDF fetch failed: {last}")


def fetch_arxiv_text(value: str) -> str:
    """Fetch text for a validated arXiv identifier or official URL."""
    ref = _parse_reference(value)
    if ref.kind == "html":
        return _fetch_html_text(_html_urls(ref.arxiv_id, ref.version))
    try:
        return _fetch_pdf_text(_pdf_urls(ref.arxiv_id, ref.version))
    except Exception:
        return _fetch_html_text(_html_urls(ref.arxiv_id, ref.version))
