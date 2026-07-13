"""Bounded subprocess worker for extracting text from untrusted PDF bytes."""

from __future__ import annotations

import io
import sys

MAX_PDF_BYTES = 25_000_000
MAX_TEXT_CHARS = 2_000_000


def _limits() -> None:
    try:
        import resource
    except ImportError:  # pragma: no cover - Windows fallback.
        return
    limits = (
        (resource.RLIMIT_CPU, 30),
        (resource.RLIMIT_AS, 1536 * 1024 * 1024),
        (resource.RLIMIT_FSIZE, MAX_TEXT_CHARS + 65_536),
        (resource.RLIMIT_NOFILE, 64),
        (resource.RLIMIT_CORE, 0),
    )
    for resource_id, requested in limits:
        try:
            _, hard = resource.getrlimit(resource_id)
            ceiling = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
            resource.setrlimit(resource_id, (ceiling, ceiling))
        except (OSError, ValueError):
            continue


def main() -> int:
    _limits()
    blob = sys.stdin.buffer.read(MAX_PDF_BYTES + 1)
    if len(blob) > MAX_PDF_BYTES or not blob.lstrip().startswith(b"%PDF"):
        return 2

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(blob))
    text = "\n".join(page.extract_text() or "" for page in reader.pages[:12])
    sys.stdout.write(text[:MAX_TEXT_CHARS])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
