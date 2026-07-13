#!/usr/bin/env python3
"""Deploy Verigraph to Butterbase: schema, data sync, API function, static frontend.

Usage:
  .venv/bin/python scripts/deploy_butterbase.py
  .venv/bin/python scripts/deploy_butterbase.py --skip-sync
  .venv/bin/python scripts/deploy_butterbase.py --frontend-only

Requires .env:
  BUTTERBASE_API_KEY, P2R_BUTTERBASE_APP_ID, BUTTERBASE_API_URL
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile

import certifi

# macOS python.org builds do not reliably inherit the system trust store.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

API = os.environ.get("BUTTERBASE_API_URL", "https://api.butterbase.ai")
APP_ID = os.environ["P2R_BUTTERBASE_APP_ID"]
KEY = os.environ["BUTTERBASE_API_KEY"]
FN_NAME = "verigraph_api"
SCHEMA_PATH = os.path.join(ROOT, "butterbase", "schema.json")
FN_PATH = os.path.join(ROOT, "butterbase", "verigraph_api.ts")
STATIC_DIR = os.path.join(ROOT, "static")


def bb_req(method: str, path: str, body: dict | None = None, timeout: int = 120, json_body: bool = True):
    url = f"{API}{path}"
    headers = {"Authorization": f"Bearer {KEY}"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    elif json_body and method in ("POST", "PATCH", "PUT"):
        headers["Content-Type"] = "application/json"
        data = b"{}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:800]
        raise RuntimeError(f"butterbase {method} {path} -> HTTP {e.code}: {detail}") from e


def ensure_schema() -> None:
    with open(SCHEMA_PATH) as f:
        schema = json.load(f)
    # Older Verigraph apps used `real` for duration while newer apps use
    # `numeric`. Both satisfy the runtime contract, so preserve an existing
    # compatible numeric type rather than requesting a destructive conversion.
    try:
        _, current = bb_req("GET", f"/v1/{APP_ID}/schema")
        current_tables = current.get("schema", {}).get("tables", {})
        numeric_types = {"smallint", "integer", "bigint", "real", "float4", "float8", "decimal", "numeric"}
        desired_tables = schema.setdefault("tables", {})
        # Schema apply treats omission as an explicit drop. Preserve objects
        # created by older deployments or other app features unless this
        # repository explicitly manages them.
        for table_name, existing_table in current_tables.items():
            if table_name not in desired_tables:
                desired_tables[table_name] = existing_table
                print(f"  preserving existing table {table_name}")
                continue
            desired_table = desired_tables[table_name]
            desired_columns = desired_table.setdefault("columns", {})
            for column_name, column in existing_table.get("columns", {}).items():
                if column_name not in desired_columns:
                    desired_columns[column_name] = column
                    print(f"  preserving existing column {table_name}.{column_name}")
            desired_indexes = desired_table.setdefault("indexes", {})
            for index_name, index in existing_table.get("indexes", {}).items():
                desired_indexes.setdefault(index_name, index)
        for table_name, table in schema.get("tables", {}).items():
            existing_columns = current_tables.get(table_name, {}).get("columns", {})
            for column_name, column in table.get("columns", {}).items():
                existing_type = existing_columns.get(column_name, {}).get("type")
                desired_type = column.get("type")
                if existing_type != desired_type and existing_type in numeric_types and desired_type in numeric_types:
                    column["type"] = existing_type
                    print(f"  preserving {table_name}.{column_name} type {existing_type}")
    except Exception:
        pass
    code, res = bb_req("POST", f"/v1/{APP_ID}/schema/apply", {"schema": schema, "dry_run": True})
    if code != 200:
        raise RuntimeError(f"schema dry_run failed: {res}")
    applied = res.get("applied", 0)
    if applied:
        print(f"  applying schema ({applied} statements)…")
        bb_req("POST", f"/v1/{APP_ID}/schema/apply", {"schema": schema, "dry_run": False})
    else:
        print("  schema up to date")


def sync_data() -> None:
    from app.butterbase import sync_workspace

    result = sync_workspace()
    print(
        "  published workspace "
        f"revision {result['revision']} ({result['papers']} papers, {result['runs']} runs)"
    )


def cognee_env_vars() -> dict[str, str]:
    """Pass private edge-function configuration from .env."""
    keys = (
        "ADMIN_TRACKING_KEY",
        "COGNEE_ENABLED",
        "COGNEE_SERVICE_URL",
        "COGNEE_API_KEY",
        "COGNEE_DATASET",
        "COGNEE_SESSION_ID",
    )
    out: dict[str, str] = {}
    for k in keys:
        v = os.environ.get(k, "").strip()
        if v:
            out[k] = v
    return out


def deploy_function() -> str:
    with open(FN_PATH) as f:
        code = f.read()
    env_vars = cognee_env_vars()
    # Remove existing function with same name if present
    try:
        code_list, res = bb_req("GET", f"/v1/{APP_ID}/functions")
        for fn in res.get("functions", []):
            if fn.get("name") == FN_NAME:
                bb_req("DELETE", f"/v1/{APP_ID}/functions/{FN_NAME}")
                print(f"  replaced existing function {FN_NAME}")
                break
    except Exception:
        pass

    body: dict = {
        "name": FN_NAME,
        "description": "Verigraph read API (graph, evidence, workspace, Cognee sessions)",
        "code": code,
        "trigger": {"type": "http", "config": {"auth": "none"}},
    }
    if env_vars:
        body["envVars"] = env_vars
        safe_keys = [k for k in env_vars if k not in ("COGNEE_API_KEY", "ADMIN_TRACKING_KEY")]
        if safe_keys:
            print(f"  function env: {', '.join(safe_keys)}")

    _, res = bb_req("POST", f"/v1/{APP_ID}/functions", body)
    url = res["url"]
    print(f"  function deployed: {url}")
    return url


def build_frontend_zip(fn_url: str) -> bytes:
    with open(os.path.join(STATIC_DIR, "config.js")) as f:
        config_js = f.read()
    if "VERIGRAPH_FN_URL" not in config_js.split("\n", 1)[0]:
        config_js = f'window.VERIGRAPH_FN_URL = "{fn_url}";\n' + config_js
    config_version = hashlib.sha256(config_js.encode()).hexdigest()[:12]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("config.js", config_js)

        for src_name, dest_name in (
            ("landing.html", "index.html"),
            ("index.html", "demo/index.html"),
            ("admin.html", "admin/index.html"),
        ):
            html = open(os.path.join(STATIC_DIR, src_name)).read()
            if 'src="/config.js"' not in html:
                html = html.replace("<head>", '<head>\n<script src="/config.js"></script>\n', 1)
            html = html.replace('src="/config.js"', f'src="/config.js?v={config_version}"')
            zf.writestr(dest_name, html)

    return buf.getvalue()


def deploy_frontend(fn_url: str) -> str:
    zip_bytes = build_frontend_zip(fn_url)
    _, res = bb_req("POST", f"/v1/{APP_ID}/frontend/deployments", {"framework": "static"})
    dep_id = res["id"]
    upload_url = res["uploadUrl"]
    print(f"  created deployment {dep_id}")

    put = urllib.request.Request(upload_url, data=zip_bytes, method="PUT", headers={"Content-Type": "application/zip"})
    with urllib.request.urlopen(put, timeout=120) as resp:
        print(f"  uploaded zip ({len(zip_bytes)} bytes) -> HTTP {resp.status}")

    bb_req("POST", f"/v1/{APP_ID}/frontend/deployments/{dep_id}/start")
    print("  started deployment, waiting for READY…")

    site_url = ""
    for _ in range(40):
        time.sleep(3)
        _, status = bb_req("GET", f"/v1/{APP_ID}/frontend/deployments")
        for dep in status.get("deployments", []):
            if dep["id"] == dep_id:
                state = dep.get("status")
                site_url = dep.get("url", site_url)
                print(f"    status: {state}")
                if state == "READY":
                    return site_url
                if state in ("FAILED", "ERROR"):
                    raise RuntimeError(f"deployment failed: {dep.get('error')}")
    raise RuntimeError("deployment timed out")


def smoke_test(fn_url: str, site_url: str) -> None:
    for route in ("health", "graph", "evidence", "workspace"):
        req = urllib.request.Request(f"{fn_url}?route={route}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.load(resp)
        print(f"  ✓ {route}: {str(body)[:80]}…")

    demo_url = site_url.rstrip("/") + "/demo/"
    for attempt, url in enumerate([site_url, demo_url, site_url.rstrip("/") + "/config.js"]):
        for retry in range(12):
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    body = resp.read(800).decode(errors="replace")
                break
            except urllib.error.HTTPError as e:
                if e.code in (403, 404, 502, 503) and retry < 11:
                    time.sleep(5)
                    continue
                raise
            except urllib.error.URLError:
                if retry < 11:
                    time.sleep(5)
                    continue
                raise
        else:
            continue
        if attempt == 0 and "Verigraph" not in body:
            raise RuntimeError(f"frontend check failed at {site_url}")
        if attempt == 2 and "VERIGRAPH_FN_URL" not in body:
            raise RuntimeError("config.js missing VERIGRAPH_FN_URL")
    print(f"  ✓ frontend live: {site_url}")
    print(f"  ✓ demo: {demo_url}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-sync", action="store_true", help="skip paper/run sync")
    ap.add_argument("--frontend-only", action="store_true", help="only redeploy static frontend")
    args = ap.parse_args()

    print(f"Deploying Verigraph to Butterbase app {APP_ID}")

    if not args.frontend_only:
        print("[1/4] Schema")
        ensure_schema()
        if not args.skip_sync:
            print("[2/4] Data sync")
            sync_data()
        else:
            print("[2/4] Data sync (skipped)")
        print("[3/4] API function")
        fn_url = deploy_function()
    else:
        fn_url = f"{API}/v1/{APP_ID}/fn/{FN_NAME}"
        print("[1-3/4] skipped (--frontend-only)")

    print("[4/4] Frontend")
    site_url = deploy_frontend(fn_url)

    print("\nSmoke test")
    smoke_test(fn_url, site_url)

    print("\n✓ Deploy complete")
    print(f"  Site:    {site_url}")
    print(f"  Demo:    {site_url.rstrip('/')}/demo/")
    print(f"  API fn:  {fn_url}")
    print("  Note: run/ask/upload need the full FastAPI backend; this deploy serves graph + evidence from Butterbase.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"✗ deploy failed: {e}", file=sys.stderr)
        sys.exit(1)
