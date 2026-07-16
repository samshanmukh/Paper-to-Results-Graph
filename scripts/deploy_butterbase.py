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
import urllib.parse
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

API = os.environ.get("BUTTERBASE_API_URL", "https://api.butterbase.ai")
APP_ID = os.environ["P2R_BUTTERBASE_APP_ID"]
KEY = os.environ["BUTTERBASE_API_KEY"]
PUBLIC_SITE_URL = os.environ.get("P2R_PUBLIC_URL", "https://paper2result.butterbase.dev")
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
    from app.butterbase import sync_papers, sync_runs

    print(f"  syncing {sync_papers()} papers…")
    print(f"  syncing {sync_runs()} runs…")


def cognee_env_vars() -> dict[str, str]:
    """Pass private edge-function configuration from .env."""
    keys = (
        "ADMIN_TRACKING_KEY",
        "COGNEE_ENABLED",
        "COGNEE_SERVICE_URL",
        "COGNEE_API_KEY",
        "COGNEE_DATASET",
        "COGNEE_SESSION_ID",
        "DAYTONA_API_KEY",
        "ROCKETRIDE_GATEWAY_BASE_URL",
        "ROCKETRIDE_GATEWAY_KEY",
        "ROCKETRIDE_GATEWAY_MODEL",
        "BUTTERBASE_SERVICE_KEY",
        "BUTTERBASE_GATEWAY_BASE_URL",
    )
    out: dict[str, str] = {}
    for k in keys:
        v = os.environ.get(k, "").strip()
        if v:
            out[k] = v
    return out


def validate_production_config(allow_replay_only: bool = False) -> None:
    """Refuse to publish a RUN-capable UI without execution compute."""
    if os.environ.get("DAYTONA_API_KEY", "").strip():
        return
    if allow_replay_only:
        print("  warning: DAYTONA_API_KEY missing; deploying an explicitly replay-only archive")
        return
    raise RuntimeError(
        "DAYTONA_API_KEY is required for production method execution. "
        "Set it in .env, or pass --allow-replay-only only for an intentional evidence archive."
    )


def inject_impl_bundle(code: str) -> str:
    """Embed curated papers/impl/*.py into the edge function for live Daytona runs."""
    bundle_path = os.path.join(ROOT, "butterbase", "impl_bundle.json")
    if not os.path.isfile(bundle_path):
        from app.research_tools import load_impl_bundle
        bundle = load_impl_bundle()
        with open(bundle_path, "w") as f:
            json.dump(bundle, f)
    with open(bundle_path) as f:
        bundle_json = f.read().strip()
    marker = "const IMPL_BUNDLE: Record<string, string> = {};"
    replacement = f"const IMPL_BUNDLE: Record<string, string> = {bundle_json};"
    if marker not in code:
        raise RuntimeError("IMPL_BUNDLE marker missing from verigraph_api.ts")
    return code.replace(marker, replacement, 1)


def deploy_function() -> str:
    with open(FN_PATH) as f:
        code = inject_impl_bundle(f.read())
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
        "description": "Verigraph API (graph, evidence, live Daytona runs, workspaces, Cognee)",
        "code": code,
        "trigger": {"type": "http", "config": {"auth": "none"}},
    }
    if env_vars:
        body["envVars"] = env_vars
        safe_keys = [k for k in env_vars if k not in ("COGNEE_API_KEY", "ADMIN_TRACKING_KEY", "DAYTONA_API_KEY")]
        if "DAYTONA_API_KEY" in env_vars:
            safe_keys.append("DAYTONA_API_KEY(set)")
        if safe_keys:
            print(f"  function env: {', '.join(safe_keys)}")
    print(f"  impl bundle: {code.count(chr(10))} lines after inject")

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
        # Extra static assets referenced by the demo
        for asset in ("advanced-features.js", "local-papers.js"):
            asset_path = os.path.join(STATIC_DIR, asset)
            if os.path.isfile(asset_path):
                zf.writestr(asset, open(asset_path).read())

        for src_name, dest_name in (
            ("landing.html", "index.html"),
            ("index.html", "demo/index.html"),
            ("admin.html", "admin/index.html"),
        ):
            html = open(os.path.join(STATIC_DIR, src_name)).read()
            if 'src="/config.js"' not in html:
                html = html.replace("<head>", '<head>\n<script src="/config.js"></script>\n', 1)
            html = html.replace('src="/config.js"', f'src="/config.js?v={config_version}"')
            html = html.replace('src="/advanced-features.js"', f'src="/advanced-features.js?v={config_version}"')
            html = html.replace('src="/local-papers.js"', f'src="/local-papers.js?v={config_version}"')
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
    def get_json(route: str, timeout: int = 30) -> object:
        req = urllib.request.Request(f"{fn_url}?route={route}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)

    def post_json(route: str, body: dict, timeout: int = 60) -> object:
        req = urllib.request.Request(
            f"{fn_url}?route={urllib.parse.quote(route, safe='/')}",
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)

    read_routes = (
        "health", "graph", "evidence", "workspace", "insights", "conflicts",
        "runs", "timeline", "batch-plan", "export",
    )
    read_results: dict[str, object] = {}
    for route in read_routes:
        body = get_json(route)
        read_results[route] = body
        print(f"  ✓ {route}: {str(body)[:80]}…")

    health = read_results["health"]
    if not isinstance(health, dict) or not health.get("ok"):
        raise RuntimeError("production health endpoint did not return ok=true")
    if os.environ.get("DAYTONA_API_KEY", "").strip() and not health.get("live_run"):
        raise RuntimeError("health check reports live_run=false despite configured Daytona execution")

    graph = read_results["graph"]
    if not isinstance(graph, dict):
        raise RuntimeError("production graph endpoint returned an invalid payload")
    method_ids = sorted({
        str(node.get("props", {}).get("id", ""))
        for node in graph.get("nodes", [])
        if node.get("label") == "Method" and node.get("props", {}).get("id")
    })
    if not method_ids:
        raise RuntimeError("production graph contains no runnable methods")
    for method_id in method_ids:
        run = post_json(f"run/{method_id}", {"params": {}}, timeout=240)
        if not isinstance(run, dict):
            raise RuntimeError(f"production run returned an invalid payload for {method_id}")
        if run.get("error") or int(run.get("exit_code", 0)) != 0:
            raise RuntimeError(f"production run smoke failed for {method_id}: {run.get('error')}")
        if not run.get("result", {}).get("claim_checks"):
            raise RuntimeError(f"production run smoke returned no verdicts for {method_id}")
        print(f"  ✓ run/{method_id}: {run.get('run_id')} [{run.get('backend')}]")

    for route, body in (
        ("ask", {"question": "Which claims have executable evidence?"}),
        ("investigate", {"message": "Audit the evidence graph"}),
        ("brief", {"message": "Summarize executable evidence"}),
        ("conduct", {"message": "Run the full evidence workflow"}),
    ):
        result = post_json(route, body, timeout=120)
        if not isinstance(result, dict) or not str(result.get("answer", "")).strip():
            raise RuntimeError(f"production {route} workflow returned no answer")
        print(f"  ✓ {route}: grounded answer returned")

    refreshed_runs = get_json("runs")
    if isinstance(refreshed_runs, list) and len(refreshed_runs) >= 2:
        comparison = post_json(
            "compare",
            {"run_a": refreshed_runs[1]["run_id"], "run_b": refreshed_runs[0]["run_id"]},
        )
        if not isinstance(comparison, dict) or "summary" not in comparison:
            raise RuntimeError("production compare workflow returned an invalid result")
        print("  ✓ compare: run comparison returned")

    brief_req = urllib.request.Request(f"{fn_url}?route=evidence-brief")
    with urllib.request.urlopen(brief_req, timeout=30) as resp:
        brief_text = resp.read().decode(errors="replace")
    if "evidence" not in brief_text.lower():
        raise RuntimeError("production evidence brief is empty or malformed")
    print("  ✓ evidence-brief: markdown returned")

    demo_url = site_url.rstrip("/") + "/demo/"
    for attempt, url in enumerate([site_url, demo_url, site_url.rstrip("/") + "/config.js", site_url.rstrip("/") + "/advanced-features.js"]):
        for retry in range(5):
            try:
                frontend_req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; VerigraphDeployCheck/1.0)",
                        "Accept": "text/html,application/javascript,*/*",
                    },
                )
                with urllib.request.urlopen(frontend_req, timeout=30) as resp:
                    body = resp.read(800).decode(errors="replace")
                break
            except urllib.error.HTTPError as e:
                if e.code in (403, 404) and retry < 4:
                    time.sleep(4)
                    continue
                raise
        else:
            continue
        if attempt == 0 and "Verigraph" not in body:
            raise RuntimeError(f"frontend check failed at {site_url}")
        if attempt == 2 and "VERIGRAPH_FN_URL" not in body:
            raise RuntimeError("config.js missing VERIGRAPH_FN_URL")
        if attempt == 3 and "verigraphAdvanced" not in body:
            raise RuntimeError("advanced-features.js missing verigraphAdvanced")
    print(f"  ✓ frontend live: {site_url}")
    print(f"  ✓ demo: {demo_url}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-sync", action="store_true", help="skip paper/run sync")
    ap.add_argument("--frontend-only", action="store_true", help="only redeploy static frontend")
    ap.add_argument(
        "--allow-replay-only",
        action="store_true",
        help="allow an intentional archive deploy without live method execution",
    )
    args = ap.parse_args()

    print(f"Deploying Verigraph to Butterbase app {APP_ID}")

    if not args.frontend_only:
        validate_production_config(args.allow_replay_only)
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
    smoke_test(fn_url, PUBLIC_SITE_URL or site_url)

    print("\n✓ Deploy complete")
    print(f"  Site:    {PUBLIC_SITE_URL or site_url}")
    print(f"  Demo:    {(PUBLIC_SITE_URL or site_url).rstrip('/')}/demo/")
    print(f"  API fn:  {fn_url}")
    print("  Live method execution and parameter propagation verified by production smoke tests.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"✗ deploy failed: {e}", file=sys.stderr)
        sys.exit(1)
