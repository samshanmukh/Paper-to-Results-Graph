"""Butterbase persistence for Verigraph: papers + run history.

The project has its own Butterbase app (created via POST /init, separate from
sceneshop's): tables `papers` and `runs`. Uses the account API key from .env.

CLI:
  python app/butterbase.py sync-papers   # upsert all extracted papers
  python app/butterbase.py sync-runs     # upsert all runs/<id>.json records
  python app/butterbase.py list          # show stored row counts
"""

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
KEY = os.environ["BUTTERBASE_API_KEY"]

EXTRACTED_DIR = os.path.join(ROOT, "papers", "extracted")
RUNS_DIR = os.path.join(ROOT, "runs")


def _req(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        f"{API}/v1/{APP_ID}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"butterbase {method} {path} -> HTTP {e.code}: {detail}")


def upsert(table: str, row: dict) -> str:
    """Insert if the id isn't stored yet; skip otherwise.

    Row-id endpoints (PATCH/DELETE /{table}/{id}) require UUID ids and our
    ids are text, so records are treated as immutable: insert-or-skip.
    """
    existing = _req("GET", f"/{table}?id=eq.{row['id']}&limit=1")
    if isinstance(existing, list) and existing:
        return "exists"
    _req("POST", f"/{table}", row)
    return "inserted"


def sync_papers() -> int:
    n = 0
    for fname in sorted(os.listdir(EXTRACTED_DIR)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(EXTRACTED_DIR, fname)) as f:
            data = json.load(f)
        p = data["paper"]
        action = upsert("papers", {
            "id": p["id"], "title": p["title"], "year": p["year"],
            "arxiv": p.get("arxiv"), "topic": p.get("topic"),
            "extraction": data,
        })
        print(f"  {action}: paper {p['id']}")
        n += 1
    return n


def sync_run(record: dict) -> str:
    result = record.get("result") or {}
    return upsert("runs", {
        "id": record["run_id"], "method_id": record["method_id"],
        "backend": record.get("backend"),
        "status": "success" if record.get("error") is None else "failure",
        "exit_code": record.get("exit_code"),
        "duration_s": record.get("duration_s"),
        "metrics": result.get("metrics"),
        # Butterbase jsonb columns reject top-level arrays; wrap in an object
        "claim_checks": {"items": result.get("claim_checks", [])},
        "params": record.get("params") or {},
        "stdout": (record.get("stdout") or "")[:4000],
        "error": record.get("error"),
    })


def sync_runs() -> int:
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
    for table in ("papers", "runs"):
        rows = _req("GET", f"/{table}?limit=100")
        items = rows if isinstance(rows, list) else rows.get("data", rows.get("rows", []))
        print(f"  {table}: {len(items)} rows")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "sync-papers":
        print(f"synced {sync_papers()} papers")
    elif cmd == "sync-runs":
        print(f"synced {sync_runs()} runs")
    elif cmd == "list":
        counts()
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
