"""Cognee semantic memory for Verigraph (alongside Neo4j).

Indexes paper text and run stdout/metrics for semantic recall via
cognee.remember() / cognee.recall(). Storage lives under .cognee/ and is
separate from the Verigraph Neo4j graph.

Enable with COGNEE_ENABLED=true and the same Butterbase gateway vars used by
app/llm.py (ROCKETRIDE_GATEWAY_*). Embeddings default to local fastembed.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import Any

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

DATASET = os.environ.get("COGNEE_DATASET", "verigraph")
_COGNEE_LOCK = threading.Lock()
_CONFIGURED = False
_MIGRATED = False
_BG_LOOP: asyncio.AbstractEventLoop | None = None
_BG_THREAD: threading.Thread | None = None


def _background_loop() -> asyncio.AbstractEventLoop:
    global _BG_LOOP, _BG_THREAD
    if _BG_LOOP is not None:
        return _BG_LOOP
    loop = asyncio.new_event_loop()

    def _runner() -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    thread = threading.Thread(target=_runner, name="cognee-loop", daemon=True)
    thread.start()
    _BG_LOOP = loop
    _BG_THREAD = thread
    return loop


def _run_async(coro, *, timeout: int = 600):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = _background_loop()
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=timeout)
    raise RuntimeError("use async Cognee helpers from async endpoints")


def _truthy(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).lower() in ("1", "true", "yes", "on")


def is_enabled() -> bool:
    if not _truthy("COGNEE_ENABLED"):
        return False
    return bool(os.environ.get("ROCKETRIDE_GATEWAY_BASE_URL") and os.environ.get("ROCKETRIDE_GATEWAY_KEY"))


def _llm_model() -> str:
    raw = os.environ.get("ROCKETRIDE_GATEWAY_MODEL", "x-ai/grok-4.3")
    if "/" in raw and not raw.startswith(("openai/", "azure/", "anthropic/", "gemini/", "xai/")):
        return f"openai/{raw}"
    return raw


def _configure_env() -> None:
    global _CONFIGURED
    with _COGNEE_LOCK:
        if _CONFIGURED:
            return
        cognee_root = os.path.join(ROOT, ".cognee")
        os.makedirs(os.path.join(cognee_root, "system"), exist_ok=True)
        os.makedirs(os.path.join(cognee_root, "data"), exist_ok=True)

        base = os.environ["ROCKETRIDE_GATEWAY_BASE_URL"].rstrip("/")
        key = os.environ["ROCKETRIDE_GATEWAY_KEY"]

        defaults = {
            "ENABLE_BACKEND_ACCESS_CONTROL": "false",
            "REQUIRE_AUTHENTICATION": "false",
            "TELEMETRY_DISABLED": "true",
            "LOG_LEVEL": os.environ.get("COGNEE_LOG_LEVEL", "ERROR"),
            "COGNEE_LOG_FILE": "false",
            "COGNEE_SKIP_CONNECTION_TEST": "true",
            "SYSTEM_ROOT_DIRECTORY": os.path.join(cognee_root, "system"),
            "DATA_ROOT_DIRECTORY": os.path.join(cognee_root, "data"),
            "LLM_PROVIDER": "openai",
            "LLM_API_KEY": key,
            "LLM_ENDPOINT": base,
            "LLM_MODEL": _llm_model(),
            "EMBEDDING_PROVIDER": os.environ.get("COGNEE_EMBEDDING_PROVIDER", "fastembed"),
            "EMBEDDING_MODEL": os.environ.get(
                "COGNEE_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
            ),
            "EMBEDDING_DIMENSIONS": os.environ.get("COGNEE_EMBEDDING_DIMENSIONS", "384"),
        }
        for k, v in defaults.items():
            os.environ.setdefault(k, v)
        _CONFIGURED = True


def _import_cognee():
    _configure_env()
    import cognee  # noqa: WPS433 — must import after env is set

    return cognee


async def _ensure_migrated(cognee) -> None:
    global _MIGRATED
    if _MIGRATED:
        return
    await cognee.run_migrations()
    _MIGRATED = True


async def remember_many(docs: list[tuple[str, list[str]]]) -> None:
    """Batch remember for sync scripts — one event loop, many documents."""
    if not is_enabled() or not docs:
        return
    cognee = _import_cognee()
    await _ensure_migrated(cognee)
    for text, node_set in docs:
        await cognee.remember(
            text,
            dataset_name=DATASET,
            node_set=node_set,
            self_improvement=False,
        )


def remember_many_sync(docs: list[tuple[str, list[str]]]) -> None:
    try:
        _run_async(remember_many(docs), timeout=3600)
    except Exception:
        pass



def _format_paper_document(paper_id: str, title: str, text: str, extraction: dict | None = None) -> str:
    lines = [f"[paper {paper_id}] {title}", "", text.strip()]
    if extraction:
        claims = extraction.get("claims") or []
        if claims:
            lines.append("\nClaims:")
            for c in claims[:12]:
                lines.append(f"- {c.get('id')}: {c.get('text')}")
    return "\n".join(lines)[:120_000]


def _format_run_document(record: dict) -> str:
    result = record.get("result") or {}
    metrics = result.get("metrics") or {}
    checks = result.get("claim_checks") or []
    stdout = (record.get("stdout") or "")[:4000]
    lines = [
        f"[run {record.get('run_id')}] method {record.get('method_id')}",
        f"backend={record.get('backend')} exit={record.get('exit_code')} duration_s={record.get('duration_s')}",
        f"metrics={json.dumps(metrics)}",
    ]
    for chk in checks:
        lines.append(f"{chk.get('verdict')} {chk.get('claim_id')}: {chk.get('detail')}")
    if stdout.strip():
        lines.append("\nstdout:\n" + stdout.strip())
    return "\n".join(lines)


def _response_text(item: Any) -> str:
    for attr in ("text", "answer", "content"):
        val = getattr(item, attr, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return str(item)


async def remember_paper(
    paper_id: str,
    title: str,
    text: str,
    extraction: dict | None = None,
) -> None:
    if not is_enabled():
        return
    cognee = _import_cognee()
    await _ensure_migrated(cognee)
    doc = _format_paper_document(paper_id, title, text, extraction)
    await cognee.remember(
        doc,
        dataset_name=DATASET,
        node_set=[paper_id, "paper"],
        self_improvement=False,
    )


async def remember_run(record: dict) -> None:
    if not is_enabled():
        return
    cognee = _import_cognee()
    await _ensure_migrated(cognee)
    method_id = record.get("method_id") or "unknown-method"
    paper_id = method_id.rsplit("-", 1)[0] if "-" in method_id else method_id
    doc = _format_run_document(record)
    await cognee.remember(
        doc,
        dataset_name=DATASET,
        node_set=[record.get("run_id", "run"), method_id, paper_id, "run"],
        self_improvement=False,
    )


async def recall(query: str, *, paper_id: str | None = None, top_k: int = 5) -> list[str]:
    if not is_enabled():
        return []
    cognee = _import_cognee()
    await _ensure_migrated(cognee)
    kwargs: dict[str, Any] = {
        "query_text": query,
        "datasets": [DATASET],
        "top_k": top_k,
        "include_references": True,
    }
    if paper_id:
        kwargs["node_name"] = [paper_id]
    try:
        results = await cognee.recall(**kwargs)
    except Exception:
        return []
    return [_response_text(r) for r in results if _response_text(r)]


async def recall_context(query: str, *, paper_id: str | None = None, top_k: int = 3) -> str:
    snippets = await recall(query, paper_id=paper_id, top_k=top_k)
    if not snippets:
        return ""
    return "\n\n---\n\n".join(snippets[:top_k])


def remember_paper_sync(*args, **kwargs) -> None:
    try:
        _run_async(remember_paper(*args, **kwargs))
    except Exception:
        pass


def remember_run_sync(record: dict) -> None:
    try:
        _run_async(remember_run(record))
    except Exception:
        pass


def recall_context_sync(query: str, *, paper_id: str | None = None, top_k: int = 3) -> str:
    try:
        return _run_async(recall_context(query, paper_id=paper_id, top_k=top_k))
    except Exception:
        return ""
