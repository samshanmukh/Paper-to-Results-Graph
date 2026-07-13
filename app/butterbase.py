"""Butterbase persistence for Verigraph: papers + run history.

The project has its own Butterbase app (created via POST /init, separate from
sceneshop's): tables `papers` and `runs`. Uses the account API key from .env.

CLI:
  python -m app.butterbase sync-workspace  # reconcile objects + publish active state
  python -m app.butterbase sync-papers     # update active paper rows only
  python -m app.butterbase sync-runs       # update active run rows only
  python -m app.butterbase list            # show stored row counts
"""

import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

import certifi

# macOS python.org builds lack system CAs (same fix as app/db.py)
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

API = os.environ.get("BUTTERBASE_API_URL", "https://api.butterbase.ai")
APP_ID = os.environ.get("P2R_BUTTERBASE_APP_ID", "app_vinjruy5c03s")
KEY = os.environ.get("BUTTERBASE_API_KEY", "")

EXTRACTED_DIR = os.path.join(ROOT, "papers", "extracted")
RUNS_DIR = os.path.join(ROOT, "runs")


def _req(method: str, path: str, body: dict | None = None):
    key = os.environ.get("BUTTERBASE_API_KEY", KEY)
    if not key:
        raise RuntimeError("BUTTERBASE_API_KEY is not configured")
    req = urllib.request.Request(
        f"{API}/v1/{APP_ID}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"butterbase {method} {path} -> HTTP {e.code}: {detail}")


def _rows(response) -> list[dict]:
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        rows = response.get("data", response.get("rows", []))
        return rows if isinstance(rows, list) else []
    return []


def _same_fields(existing: dict, row: dict) -> bool:
    return all(existing.get(key) == value for key, value in row.items())


def _all_rows(table: str, *, select: str = "*") -> list[dict]:
    rows: list[dict] = []
    page_size = 500
    offset = 0
    while True:
        page = _rows(
            _req(
                "GET",
                f"/{table}?select={select}&order=id&limit={page_size}&offset={offset}",
            )
        )
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size


def upsert(table: str, row: dict) -> str:
    """Insert or PATCH a row by its declared primary key.

    Butterbase row endpoints accept text primary keys, so existing paper
    revisions and legacy runs are reconciled instead of silently skipped.
    """
    existing = _req("GET", f"/{table}?id=eq.{row['id']}&limit=1")
    rows = _rows(existing)
    if rows:
        if _same_fields(rows[0], row):
            return "unchanged"
        row_id = urllib.parse.quote(str(row["id"]), safe="")
        _req("PATCH", f"/{table}/{row_id}", {k: v for k, v in row.items() if k != "id"})
        return "updated"
    _req("POST", f"/{table}", row)
    return "inserted"


def _json_digest(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _paper_row(data: dict, entry: dict, revision: int) -> dict:
    p = data["paper"]
    source = entry.get("source") or {}
    return {
        "id": p["id"],
        "title": p["title"],
        "year": p["year"],
        "arxiv": p.get("arxiv"),
        "topic": p.get("topic"),
        "extraction": data,
        "content_digest": _json_digest(data),
        "workspace_revision": revision,
        "active": True,
        "source_kind": source.get("kind"),
        "source_object": source.get("object"),
    }


def _workspace_snapshot_unlocked(store) -> dict:
    """Read one snapshot while the caller owns the workspace lock."""
    manifest = store.manifest()
    entries = list(manifest["active_papers"])
    papers = [store.read_paper(entry) for entry in entries]
    active_methods = {
        method["id"]
        for paper in papers
        for method in paper.get("methods", [])
        if isinstance(method, dict) and isinstance(method.get("id"), str)
    }
    runs: list[dict] = []
    if store.runs_dir.is_dir():
        for path in sorted(store.runs_dir.glob("*.json")):
            with path.open(encoding="utf-8") as handle:
                record = json.load(handle)
            if not isinstance(record, dict):
                raise ValueError(f"run record must be an object: {path}")
            if record.get("method_id") in active_methods:
                if not isinstance(record.get("run_id"), str) or not record["run_id"]:
                    raise ValueError(f"run record is missing run_id: {path}")
                runs.append(record)
    identity = _json_digest(
        {
            "manifest": manifest,
            "runs": sorted(
                (
                    {
                        "id": record["run_id"],
                        "digest": _json_digest(record),
                    }
                    for record in runs
                ),
                key=lambda item: item["id"],
            ),
        }
    )
    return {
        "manifest": manifest,
        "entries": entries,
        "papers": papers,
        "runs": runs,
        "identity": identity,
    }


def _workspace_snapshot() -> dict:
    from app.workspace import get_workspace_store

    store = get_workspace_store()
    with store.lock():
        return _workspace_snapshot_unlocked(store)


def _sync_paper_rows(snapshot: dict) -> int:
    revision = snapshot["manifest"]["revision"]
    active_ids: set[str] = set()
    for data, entry in zip(snapshot["papers"], snapshot["entries"], strict=True):
        row = _paper_row(data, entry, revision)
        action = upsert("papers", row)
        active_ids.add(row["id"])
        print(f"  {action}: paper {row['id']}@{row['content_digest'][:12]}")

    existing = _all_rows("papers", select="id,active")
    for row in existing:
        paper_id = row.get("id")
        if isinstance(paper_id, str) and paper_id not in active_ids and row.get("active") is not False:
            upsert(
                "papers",
                {
                    "id": paper_id,
                    "active": False,
                    "workspace_revision": revision,
                },
            )
    return len(active_ids)


def sync_papers() -> int:
    from app.workspace import get_workspace_store

    store = get_workspace_store()
    with store.lock():
        return _sync_paper_rows(_workspace_snapshot_unlocked(store))


def sync_run(record: dict, *, active: bool = True) -> str:
    from app.curator import validate_run_record

    record = validate_run_record(record)
    result = record.get("result") or {}
    source = record.get("implementation_source") or "unknown"
    params = record.get("params")
    parameter_overrides = record.get("parameter_overrides")
    params = {} if params is None else params
    parameter_overrides = {} if parameter_overrides is None else parameter_overrides
    if not isinstance(params, dict) or not isinstance(parameter_overrides, dict):
        raise ValueError("run params and parameter_overrides must be objects")
    return upsert("runs", {
        "id": record["run_id"], "method_id": record["method_id"],
        "backend": record.get("backend"),
        "status": "success" if record.get("error") is None else "failure",
        "exit_code": record.get("exit_code"),
        "duration_s": record.get("duration_s"),
        "created_at": record.get("created_at"),
        "implementation_source": source,
        "implementation_fingerprint": record.get("implementation_fingerprint"),
        "context_digest": record.get("context_digest"),
        "workspace_revision": record.get("workspace_revision"),
        "active": active,
        "provisional": bool(
            record.get("provisional")
            or source != "curated"
            or not record.get("implementation_fingerprint")
            or not record.get("context_digest")
        ),
        "params": params,
        "parameter_overrides": parameter_overrides,
        "metrics": result.get("metrics"),
        # Butterbase jsonb columns reject top-level arrays; wrap in an object
        "claim_checks": {"items": result.get("claim_checks", [])},
        "params": record.get("params") or {},
        "stdout": (record.get("stdout") or "")[:4000],
        "error": record.get("error"),
    })


def sync_runs() -> int:
    from app.workspace import get_workspace_store

    store = get_workspace_store()
    with store.lock():
        n = 0
        if not os.path.isdir(RUNS_DIR):
            return 0
        for fname in sorted(os.listdir(RUNS_DIR)):
            if not fname.endswith(".json"):
                continue
            with open(os.path.join(RUNS_DIR, fname)) as f:
                record = json.load(f)
            action = sync_run(record)
            print(f"  {action}: run {record['run_id']}")
            n += 1
        return n


def _workspace_state_row(snapshot: dict) -> dict:
    revision = snapshot["manifest"]["revision"]
    paper_refs = [
        {
            "id": data["paper"]["id"],
            "content_digest": _json_digest(data),
        }
        for data in snapshot["papers"]
    ]
    return {
        "id": "default",
        "revision": revision,
        "manifest_digest": _json_digest(snapshot["manifest"]),
        "active_papers": {"items": paper_refs},
        "active_run_ids": {
            "items": sorted(record["run_id"] for record in snapshot["runs"])
        },
        "snapshot_digest": snapshot["identity"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def sync_workspace() -> dict:
    """Reconcile cloud objects, then publish one authoritative active snapshot."""
    from app.workspace import get_workspace_store

    store = get_workspace_store()
    # Keep the lock during network I/O intentionally. Every remote mutation
    # must belong to the snapshot that is ultimately published; otherwise an
    # older overlapping sync can overwrite rows referenced by a newer state.
    with store.lock():
        snapshot = _workspace_snapshot_unlocked(store)
        paper_count = _sync_paper_rows(snapshot)
        run_count = 0
        for record in snapshot["runs"]:
            action = sync_run(record)
            print(f"  {action}: run {record['run_id']}")
            run_count += 1
        active_run_ids = {record["run_id"] for record in snapshot["runs"]}
        existing_runs = _all_rows("runs", select="id,active")
        for row in existing_runs:
            run_id = row.get("id")
            if (
                isinstance(run_id, str)
                and run_id not in active_run_ids
                and row.get("active") is not False
            ):
                upsert("runs", {"id": run_id, "active": False})

        state_action = upsert("workspace_state", _workspace_state_row(snapshot))
    return {
        "papers": paper_count,
        "runs": run_count,
        "revision": snapshot["manifest"]["revision"],
        "state": state_action,
    }


def register_visitor(email: str, location: dict, visitor_timezone: str = "") -> dict:
    """Create or refresh an email-gated demo visitor."""
    now = datetime.now(timezone.utc).isoformat()
    query = urllib.parse.urlencode({"email": f"eq.{email}", "limit": 1})
    existing = _req("GET", f"/demo_visitors?{query}")
    rows = existing if isinstance(existing, list) else existing.get("data", existing.get("rows", []))
    fields = {
        "ip_address": location.get("ip", ""),
        "region": location.get("region", ""),
        "country": location.get("country", ""),
        "city": location.get("city", ""),
        "timezone": visitor_timezone[:100],
        "last_seen": now,
        "last_tool": "demo_opened",
    }
    if rows:
        visitor = rows[0]
        _req("PATCH", f"/demo_visitors/{visitor['id']}", fields)
        return {"id": visitor["id"], "email": visitor["email"]}

    visitor = {"id": str(uuid.uuid4()), "email": email, "first_seen": now, **fields, "tool_uses": 0}
    _req("POST", "/demo_visitors", visitor)
    return {"id": visitor["id"], "email": email}


def record_visitor_tool(visitor_id: str, tool: str) -> None:
    """Update the latest action for a known local-demo visitor."""
    query = urllib.parse.urlencode({"id": f"eq.{visitor_id}", "limit": 1})
    existing = _req("GET", f"/demo_visitors?{query}")
    rows = existing if isinstance(existing, list) else existing.get("data", existing.get("rows", []))
    if not rows:
        return
    visitor = rows[0]
    _req(
        "PATCH",
        f"/demo_visitors/{visitor_id}",
        {
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "last_tool": tool[:80],
            "tool_uses": int(visitor.get("tool_uses") or 0) + 1,
        },
    )


def list_visitors() -> list[dict]:
    rows = _req("GET", "/demo_visitors?order=last_seen.desc&limit=500")
    return rows if isinstance(rows, list) else rows.get("data", rows.get("rows", []))


def update_visitor_profile(visitor_id: str, display_name: str) -> None:
    _req(
        "PATCH",
        f"/demo_visitors/{visitor_id}",
        {
            "display_name": display_name[:80],
            "last_seen": datetime.now(timezone.utc).isoformat(),
        },
    )


def list_workspaces(visitor_id: str = "", email: str = "") -> list[dict]:
    if visitor_id:
        q = urllib.parse.urlencode({"visitor_id": f"eq.{visitor_id}", "order": "updated_at.desc", "limit": 50})
    elif email:
        q = urllib.parse.urlencode({"email": f"eq.{email}", "order": "updated_at.desc", "limit": 50})
    else:
        return []
    rows = _req("GET", f"/workspaces?{q}")
    return rows if isinstance(rows, list) else rows.get("data", rows.get("rows", []))


def save_workspace(name: str, snapshot: dict, visitor_id: str | None = None, email: str | None = None) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "id": str(uuid.uuid4()),
        "name": name[:80],
        "snapshot": snapshot or {},
        "visitor_id": visitor_id,
        "email": email,
        "created_at": now,
        "updated_at": now,
    }
    _req("POST", "/workspaces", row)
    return row


def delete_workspace(workspace_id: str, visitor_id: str | None = None) -> None:
    if visitor_id:
        q = urllib.parse.urlencode({"id": f"eq.{workspace_id}", "visitor_id": f"eq.{visitor_id}", "limit": 1})
        existing = _req("GET", f"/workspaces?{q}")
        rows = existing if isinstance(existing, list) else existing.get("data", existing.get("rows", []))
        if not rows:
            raise RuntimeError("workspace not found")
    _req("DELETE", f"/workspaces/{workspace_id}")


def counts():
    for table in ("papers", "runs", "workspace_state"):
        rows = _req("GET", f"/{table}?limit=100")
        items = rows if isinstance(rows, list) else rows.get("data", rows.get("rows", []))
        print(f"  {table}: {len(items)} rows")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "sync-papers":
        print(f"synced {sync_papers()} papers")
    elif cmd == "sync-runs":
        print(f"synced {sync_runs()} runs")
    elif cmd == "sync-workspace":
        print(json.dumps(sync_workspace(), indent=2))
    elif cmd == "list":
        counts()
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
