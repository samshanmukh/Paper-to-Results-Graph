"""Workspace orchestration backed by an atomic runtime manifest.

The checked-in ``papers`` tree is a read-only corpus.  New, demo, reset, and
remove operations only switch the active manifest and archive runtime runs;
they never copy over or delete research assets tracked by git.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from contextlib import contextmanager
from typing import Iterator

from app.curator import curate_run
from app.db import DATABASE, GRAPH_NAMESPACE, GRAPH_OWNER_LABEL, get_driver
from app.graph import load_claim_relations, load_paper
from app.queries import run_query
from app.validation import normalize_extraction
from app.workspace_storage import (
    ROOT as STORAGE_ROOT,
    WorkspaceStorageError,
    WorkspaceStore,
    validate_paper_id,
)

ROOT = str(STORAGE_ROOT)
IMPL_DIR = os.path.join(ROOT, "papers", "impl")
BUNDLED_DIR = os.path.join(ROOT, "papers", "bundled")
BUNDLED_EXTRACTED = os.path.join(BUNDLED_DIR, "extracted")
BUNDLED_TEXTS = os.path.join(BUNDLED_DIR, "texts")
BUNDLED_IMPL = os.path.join(BUNDLED_DIR, "impl")

# Core hackathon demo: three papers that disagree about Adam.
DEMO_PAPER_IDS = ("adam2014", "wilson2017", "adamw2017")


def get_workspace_store() -> WorkspaceStore:
    """Return the default store; kept injectable for isolated tests and tools."""
    return WorkspaceStore()


def _paper_manifest(store: WorkspaceStore | None = None) -> list[dict]:
    store = store or get_workspace_store()
    out = []
    for entry in store.active_entries():
        data = store.read_paper(entry)
        paper = data["paper"]
        out.append(
            {
                "id": paper["id"],
                "title": paper["title"],
                "year": paper.get("year"),
                "arxiv": paper.get("arxiv"),
                "claims": len(data.get("claims", [])),
                "methods": len(data.get("methods", [])),
                "source": entry["source"]["kind"],
            }
        )
    return out


def active_papers() -> list[dict]:
    """Return extracted metadata for the current atomic manifest snapshot."""
    return get_workspace_store().active_papers()


def active_method_ids() -> set[str]:
    """Return method ids belonging to papers selected by the active manifest."""
    return {
        method["id"]
        for paper in active_papers()
        for method in paper.get("methods", [])
        if isinstance(method, dict) and isinstance(method.get("id"), str)
    }


def require_active_method(method_id: str) -> dict:
    """Return active method metadata or reject stale/inactive implementations."""
    for paper in active_papers():
        for method in paper.get("methods", []):
            if isinstance(method, dict) and method.get("id") == method_id:
                return method
    raise FileNotFoundError(f"method is not in the active workspace: {method_id}")


@contextmanager
def active_method_guard(method_id: str) -> Iterator[dict]:
    """Prevent workspace transitions while a validated method is being run."""
    store = get_workspace_store()
    with store.lock():
        for paper in store.active_papers():
            for method in paper.get("methods", []):
                if isinstance(method, dict) and method.get("id") == method_id:
                    yield method
                    return
        raise FileNotFoundError(f"method is not in the active workspace: {method_id}")


def _read_run_records(store: WorkspaceStore, papers: list[dict]) -> list[dict]:
    method_ids = {
        method["id"]
        for paper in papers
        for method in paper.get("methods", [])
        if isinstance(method, dict) and isinstance(method.get("id"), str)
    }
    records = []
    if not store.runs_dir.is_dir():
        return records
    for path in sorted(store.runs_dir.glob("*.json")):
        try:
            with path.open(encoding="utf-8") as handle:
                record = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceStorageError(f"cannot restore run {path}: {exc}") from exc
        if not isinstance(record, dict):
            raise WorkspaceStorageError(f"run record must be an object: {path}")
        if record.get("method_id") in method_ids:
            records.append(record)
    return records


def _replace_graph(
    store: WorkspaceStore, *, allow_manifest_recovery: bool = False
) -> dict:
    """Atomically replace graph state with the manifest and active run files."""
    papers = store.active_papers(
        allow_manifest_recovery=allow_manifest_recovery
    )
    runs = _read_run_records(store, papers)

    def replace(tx) -> None:
        tx.run(
            f"MATCH (n:{GRAPH_OWNER_LABEL} "
            "{verigraph_namespace: $graph_namespace}) DETACH DELETE n",
            graph_namespace=GRAPH_NAMESPACE,
        )
        for data in papers:
            load_paper(tx, data)
        for data in papers:
            load_claim_relations(tx, data)
        for record in runs:
            curate_run(tx, record)

    with get_driver() as driver, driver.session(database=DATABASE) as session:
        session.execute_write(replace)

    return {
        "papers": len(papers),
        "claims": sum(len(data.get("claims", [])) for data in papers),
        "methods": sum(len(data.get("methods", [])) for data in papers),
        "runs": len(runs),
    }


def _sync_failure(pending: dict, error: Exception) -> dict:
    return {
        "ok": False,
        "partial": True,
        "applied": True,
        "sync": {
            "state": "pending_graph_recovery",
            "operation_id": pending["operation_id"],
            "stage": "graph",
            "error": f"{type(error).__name__}: {error}",
            "recovery": "run `python3 -m app.restore` to reconcile Neo4j from the manifest",
        },
    }


def _recover_pending_locked(store: WorkspaceStore) -> dict | None:
    pending = store.recover_storage()
    if pending is None:
        return None
    manifest_recovery = pending.get("manifest_recovery")
    if pending.get("phase") == "rolled_back":
        stats = _replace_graph(
            store, allow_manifest_recovery=manifest_recovery is not None
        )
        if manifest_recovery is not None:
            store.complete_manifest_recovery(manifest_recovery)
        return {
            "ok": True,
            "partial": False,
            "applied": False,
            "recovered_operation": pending["operation_id"],
            "recovery_action": "storage_rolled_back",
            **stats,
        }
    try:
        stats = _replace_graph(
            store, allow_manifest_recovery=manifest_recovery is not None
        )
    except Exception as exc:
        return _sync_failure(pending, exc)
    active_pending = store.pending()
    if active_pending is not None:
        store.complete_transition(active_pending)
    if manifest_recovery is not None:
        store.complete_manifest_recovery(manifest_recovery)
    return {
        "ok": True,
        "partial": False,
        "applied": True,
        "recovered_operation": pending["operation_id"],
        **stats,
    }


def _apply_transition(
    entries: list[dict]
    | Callable[[WorkspaceStore], tuple[list[dict], set[str] | None]],
    operation: str,
    *,
    archive_method_ids: set[str] | None = frozenset(),
) -> dict:
    """Commit files first, then atomically sync the graph, journaling failures."""
    store = get_workspace_store()
    with store.lock():
        recovered = _recover_pending_locked(store)
        if recovered and recovered.get("partial"):
            return {
                **recovered,
                "applied": False,
                "blocked_operation": operation,
                "message": "A prior workspace change is still awaiting graph recovery.",
            }

        if callable(entries):
            target_entries, target_archive_method_ids = entries(store)
        else:
            target_entries = entries
            target_archive_method_ids = archive_method_ids
        pending = store.prepare_transition(target_entries, operation)
        try:
            archived = store.archive_runtime(
                pending,
                method_ids=target_archive_method_ids,
                include_generated=(
                    target_archive_method_ids is None or bool(target_archive_method_ids)
                ),
            )
            store.commit_transition(pending)
        except Exception:
            # Before the manifest switch, a transition is all-or-nothing.
            current = store.manifest()
            if current["revision"] == pending["from_revision"]:
                store.rollback_uncommitted(pending)
            raise

        try:
            stats = _replace_graph(store)
        except Exception as exc:
            papers = store.active_papers()
            return {
                **_sync_failure(pending, exc),
                "archived_runtime_files": archived,
                "papers": len(papers),
                "claims": sum(len(data.get("claims", [])) for data in papers),
                "methods": sum(len(data.get("methods", [])) for data in papers),
            }

        store.complete_transition(pending)
        return {
            "ok": True,
            "partial": False,
            "applied": True,
            "sync": {"state": "synchronized", "operation_id": pending["operation_id"]},
            "archived_runtime_files": archived,
            **stats,
        }


def recover_workspace() -> dict:
    """Finish a crashed transition or reconcile a committed manifest to Neo4j."""
    store = get_workspace_store()
    with store.lock():
        result = _recover_pending_locked(store)
        if result is not None:
            return result
        stats = _replace_graph(store)
        return {
            "ok": True,
            "partial": False,
            "applied": True,
            "recovered_operation": None,
            **stats,
        }


def persist_paper_and_activate(data: dict, text: str) -> dict:
    """Atomically persist an upload, activate it, and reconcile the full graph.

    The immutable object write happens before the manifest transition.  A graph
    failure leaves the committed manifest and pending journal available to
    ``recover_workspace``/``restore_all`` rather than silently diverging.
    """
    data = normalize_extraction(data)
    store = get_workspace_store()
    paper_id = validate_paper_id(data["paper"].get("id"))
    entry = store.persist_runtime_paper(data, text)

    def activate(latest_store: WorkspaceStore) -> tuple[list[dict], set[str]]:
        current = latest_store.active_entries()
        previous = next((item for item in current if item["id"] == paper_id), None)
        try:
            max_papers = max(1, min(500, int(os.environ.get("VERIGRAPH_MAX_ACTIVE_PAPERS", "50"))))
        except ValueError:
            max_papers = 50
        if previous is None and len(current) >= max_papers:
            raise WorkspaceStorageError(
                f"workspace is limited to {max_papers} active papers"
            )
        previous_methods: set[str] = set()
        if previous is not None:
            previous_methods = {
                method["id"]
                for method in latest_store.read_paper(previous).get("methods", [])
                if isinstance(method, dict) and isinstance(method.get("id"), str)
            }
        target = [item for item in current if item["id"] != paper_id] + [entry]
        return target, previous_methods

    result = _apply_transition(
        activate,
        f"activate-{paper_id}",
    )
    return {
        "paper_id": paper_id,
        "title": data["paper"].get("title", paper_id),
        "claims": len(data.get("claims", [])),
        "methods": len(data.get("methods", [])),
        "method_ids": [
            method["id"] for method in data.get("methods", []) if "id" in method
        ],
        "relations": len(data.get("claim_relations", [])),
        "workspace": result,
    }


# Alternate wording for callers migrating from direct file writes.
persist_and_activate_paper = persist_paper_and_activate


def load_extracted_to_graph() -> dict:
    """MERGE active manifest papers without clearing graph state (legacy helper)."""
    store = get_workspace_store()
    papers = store.active_papers()
    with get_driver() as driver, driver.session(database=DATABASE) as session:
        for data in papers:
            session.execute_write(load_paper, data)
        for data in papers:
            session.execute_write(load_claim_relations, data)
        rows = run_query(driver, "evidence")
    return {
        "papers": len(papers),
        "claims": len(rows),
        "methods": sum(len(data.get("methods", [])) for data in papers),
    }


def clear_workspace_files() -> None:
    """Compatibility no-op: checked-in workspace assets are immutable."""


def clear_run_dirs() -> int:
    """Archive all runtime files and reconcile Neo4j (legacy helper)."""
    result = reset_workspace_runs()
    if not result.get("ok"):
        error = result.get("sync", {}).get("error", "workspace run reset failed")
        raise WorkspaceStorageError(error)
    return result.get("archived_runtime_files", 0)


def copy_bundled_to_workspace(
    paper_ids: tuple[str, ...] | None = DEMO_PAPER_IDS,
) -> int:
    """Return available bundled count; assets are selected, never copied."""
    return len(get_workspace_store().bundled_entries(paper_ids))


def remove_paper(paper_id: str) -> dict:
    """Deactivate one paper, archive its runs, and atomically rebuild the graph."""
    paper_id = validate_paper_id(paper_id)
    store = get_workspace_store()
    removed: dict = {}

    def deactivate(latest_store: WorkspaceStore) -> tuple[list[dict], set[str]]:
        current = latest_store.active_entries()
        entry = next((item for item in current if item["id"] == paper_id), None)
        if entry is None:
            raise FileNotFoundError(f"paper not in workspace: {paper_id}")
        data = latest_store.read_paper(entry)
        removed["data"] = data
        method_ids = {
            method["id"]
            for method in data.get("methods", [])
            if isinstance(method, dict) and isinstance(method.get("id"), str)
        }
        remaining = [item for item in current if item["id"] != paper_id]
        return remaining, method_ids

    result = _apply_transition(
        deactivate,
        f"remove-{paper_id}",
    )
    if not result.get("applied"):
        return {
            **result,
            "removed": None,
            "requested_paper_id": paper_id,
            "message": "Paper removal was not applied because graph recovery is pending.",
        }
    data = removed["data"]
    detail = _paper_manifest(store)
    return {
        **result,
        "removed": paper_id,
        "title": data["paper"].get("title", paper_id),
        "runs_removed": result.get("archived_runtime_files", 0),
        "empty": not detail,
        "paper_ids": [paper["id"] for paper in detail],
        "papers_detail": detail,
        "message": f"Removed {paper_id} from the active workspace; source assets were preserved.",
    }


def workspace_status() -> dict:
    store = get_workspace_store()
    with store.lock():
        manifest = _paper_manifest(store)
        pending = store.pending()
        empty = not manifest
        with get_driver() as driver:
            rows = run_query(driver, "evidence") if not empty else []
            runs, _, _ = driver.execute_query(
                "MATCH (n:Run:Verigraph {verigraph_namespace: $graph_namespace}) "
                "RETURN count(n) AS c",
                graph_namespace=GRAPH_NAMESPACE,
                database_=DATABASE,
            )
        current_manifest = store.manifest()
        pending_state = None
        if pending:
            target_revision = pending.get("target_manifest", {}).get("revision")
            pending_state = (
                "pending_graph_recovery"
                if current_manifest["revision"] == target_revision
                else "pending_storage_recovery"
            )
        return {
            "empty": empty,
            "papers": len(manifest),
            "claims": len(rows),
            "runs": runs[0]["c"] if runs else 0,
            "paper_ids": [paper["id"] for paper in manifest],
            "papers_detail": manifest,
            "revision": current_manifest["revision"],
            "sync": (
                {
                    "state": pending_state,
                    "operation_id": pending["operation_id"],
                    "phase": pending["phase"],
                }
                if pending
                else {"state": "synchronized"}
            ),
        }


def new_workspace() -> dict:
    """Activate a blank manifest and archive runtime outputs; preserve sources."""
    result = _apply_transition([], "new-workspace", archive_method_ids=None)
    if not result.get("applied"):
        return {
            **result,
            "message": "New workspace was not applied because graph recovery is pending.",
        }
    return {
        **result,
        "empty": True,
        "papers": 0,
        "claims": 0,
        "message": "New workspace ready - add your first paper.",
    }


def reset_workspace_runs() -> dict:
    """Archive every run/generated artifact and rebuild the active paper graph."""
    result = _apply_transition(
        lambda store: (store.active_entries(), None),
        "reset-workspace-runs",
    )
    if not result.get("applied"):
        return {
            **result,
            "pristine": False,
            "message": "Reset was not applied because graph recovery is pending.",
        }
    return {
        **result,
        "empty": result.get("papers", 0) == 0,
        "pristine": bool(result.get("ok")),
    }


def load_demo_workspace() -> dict:
    """Select bundled demo papers in a fresh graph without copying tracked files."""
    store = get_workspace_store()
    entries = store.bundled_entries(DEMO_PAPER_IDS)
    result = _apply_transition(entries, "load-demo", archive_method_ids=None)
    if not result.get("applied"):
        return {
            **result,
            "message": "Demo load was not applied because graph recovery is pending.",
        }
    return {
        **result,
        "empty": False,
        "bundled": len(entries),
        "message": (
            f"Demo loaded - {len(entries)} papers (Adam, Wilson, AdamW), no runs yet."
        ),
    }
