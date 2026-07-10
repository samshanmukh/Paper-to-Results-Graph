"""Cognee semantic memory for Verigraph (alongside Neo4j).

Indexes paper text and run stdout/metrics for semantic recall via
cognee.remember() / cognee.recall(). Separate from the Verigraph Neo4j graph.

Local mode (default): storage under .cognee/, fastembed embeddings, Butterbase
gateway for LLM. Set COGNEE_ENABLED=true + ROCKETRIDE_GATEWAY_*.

Cloud mode (opt-in): routes to Cognee Cloud via cognee.serve() so Sessions and
Brain populate on platform.cognee.ai. Set COGNEE_ENABLED=true, COGNEE_CLOUD=true,
COGNEE_SERVICE_URL, and COGNEE_API_KEY (from the dashboard API Keys page).
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

_COGNEE_LOCK = threading.Lock()
_CONFIGURED = False
_MIGRATED = False
_CLOUD_CONNECTED = False
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


def is_cloud_mode() -> bool:
    return _truthy("COGNEE_CLOUD")


def _cloud_url() -> str:
    return (os.environ.get("COGNEE_SERVICE_URL") or os.environ.get("COGNEE_BASE_URL") or "").rstrip("/")


def _cloud_credentials_ok() -> bool:
    return bool(_cloud_url() and os.environ.get("COGNEE_API_KEY"))


def dataset_name() -> str:
    explicit = os.environ.get("COGNEE_DATASET")
    if explicit:
        return explicit
    return "default_dataset" if is_cloud_mode() else "verigraph"


def is_enabled() -> bool:
    if not _truthy("COGNEE_ENABLED"):
        return False
    if is_cloud_mode():
        return _cloud_credentials_ok()
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
        defaults: dict[str, str] = {
            "LOG_LEVEL": os.environ.get("COGNEE_LOG_LEVEL", "ERROR"),
            "COGNEE_LOG_FILE": "false",
            "COGNEE_SKIP_CONNECTION_TEST": "true",
        }
        if is_cloud_mode():
            defaults.update(
                {
                    "COGNEE_SERVICE_URL": _cloud_url(),
                    "COGNEE_API_KEY": os.environ.get("COGNEE_API_KEY", ""),
                }
            )
        else:
            cognee_root = os.path.join(ROOT, ".cognee")
            os.makedirs(os.path.join(cognee_root, "system"), exist_ok=True)
            os.makedirs(os.path.join(cognee_root, "data"), exist_ok=True)
            base = os.environ["ROCKETRIDE_GATEWAY_BASE_URL"].rstrip("/")
            key = os.environ["ROCKETRIDE_GATEWAY_KEY"]
            defaults.update(
                {
                    "ENABLE_BACKEND_ACCESS_CONTROL": "false",
                    "REQUIRE_AUTHENTICATION": "false",
                    "TELEMETRY_DISABLED": "true",
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
            )
        for k, v in defaults.items():
            if v:
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


async def _ensure_connected(cognee) -> None:
    global _CLOUD_CONNECTED
    if is_cloud_mode():
        if _CLOUD_CONNECTED:
            return
        url = _cloud_url()
        api_key = os.environ.get("COGNEE_API_KEY", "")
        await cognee.serve(url=url, api_key=api_key)
        _CLOUD_CONNECTED = True
        return
    await _ensure_migrated(cognee)


def _remember_kwargs(*, node_set: list[str] | None = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "dataset_name": dataset_name(),
        "self_improvement": False,
    }
    if node_set:
        kwargs["node_set"] = node_set
    session_id = os.environ.get("COGNEE_SESSION_ID")
    if is_cloud_mode():
        kwargs["session_id"] = session_id or "verigraph"
    elif session_id:
        kwargs["session_id"] = session_id
    return kwargs


async def remember_many(docs: list[tuple[str, list[str]]]) -> None:
    """Batch remember for sync scripts — one event loop, many documents."""
    if not is_enabled() or not docs:
        return
    cognee = _import_cognee()
    await _ensure_connected(cognee)
    for text, node_set in docs:
        await cognee.remember(text, **_remember_kwargs(node_set=node_set))


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
    await _ensure_connected(cognee)
    doc = _format_paper_document(paper_id, title, text, extraction)
    await cognee.remember(doc, **_remember_kwargs(node_set=[paper_id, "paper"]))


async def remember_run(record: dict) -> None:
    if not is_enabled():
        return
    cognee = _import_cognee()
    await _ensure_connected(cognee)
    method_id = record.get("method_id") or "unknown-method"
    paper_id = method_id.rsplit("-", 1)[0] if "-" in method_id else method_id
    doc = _format_run_document(record)
    await cognee.remember(
        doc,
        **_remember_kwargs(node_set=[record.get("run_id", "run"), method_id, paper_id, "run"]),
    )


async def recall(query: str, *, paper_id: str | None = None, top_k: int = 5) -> list[str]:
    if not is_enabled():
        return []
    cognee = _import_cognee()
    await _ensure_connected(cognee)
    kwargs: dict[str, Any] = {
        "query_text": query,
        "datasets": [dataset_name()],
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


async def log_session_qa(question: str, answer: str, *, session_id: str | None = None) -> None:
    """Write a qa entry so the session appears on platform.cognee.ai Sessions tab."""
    if not is_enabled() or not is_cloud_mode():
        return
    import urllib.error
    import urllib.request

    url = f"{_cloud_url()}/api/v1/remember/entry"
    payload = {
        "entry": {"type": "qa", "question": question, "answer": answer[:8000]},
        "dataset_name": dataset_name(),
        "session_id": session_id or os.environ.get("COGNEE_SESSION_ID") or "verigraph-cloud-demo",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": os.environ.get("COGNEE_API_KEY", ""),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()
    except Exception:
        pass


def log_session_qa_sync(question: str, answer: str) -> None:
    try:
        _run_async(log_session_qa(question, answer), timeout=90)
    except Exception:
        pass
