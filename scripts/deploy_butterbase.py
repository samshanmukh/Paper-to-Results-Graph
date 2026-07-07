"""Deploy deploy/index.html to https://paper2result.butterbase.dev.

Flow (Butterbase frontend deployment over MCP):
  1. sync papers + runs so the live data is current
  2. create_frontend_deployment (framework: static) -> deployment_id + uploadUrl
  3. PUT the zip to the signed URL
  4. manage_frontend start_deployment -> READY

Usage: .venv/bin/python scripts/deploy_butterbase.py
"""

import json
import os
import subprocess
import sys
import urllib.request

import certifi

os.environ.setdefault("SSL_CERT_FILE", certifi.where())

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

MCP = os.environ["BUTTERBASE_MCP_URL"]
KEY = os.environ["BUTTERBASE_API_KEY"]
APP = os.environ.get("P2R_BUTTERBASE_APP_ID", "app_vinjruy5c03s")
DEPLOY_DIR = os.path.join(ROOT, "deploy")


def rpc(method: str, params: dict, rid: int):
    req = urllib.request.Request(
        MCP,
        data=json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                         "params": params}).encode(),
        headers={"Authorization": f"Bearer {KEY}",
                 "Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"},
        method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read().decode()
    for line in body.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:])
    return json.loads(body)


def tool(name: str, args: dict, rid: int) -> dict:
    r = rpc("tools/call", {"name": name, "arguments": args}, rid)
    text = "".join(c.get("text", "") for c in r["result"]["content"])
    return json.loads(text)


def main() -> int:
    from app.butterbase import sync_papers, sync_runs
    print(f"syncing data: {sync_papers()} papers, {sync_runs()} runs")

    zip_path = os.path.join(DEPLOY_DIR, "site.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    subprocess.run(["zip", "-q", "-X", "site.zip", "index.html"],
                   cwd=DEPLOY_DIR, check=True)

    rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "p2r-deploy", "version": "1"}}, 1)
    dep = tool("create_frontend_deployment",
               {"app_id": APP, "framework": "static"}, 2)
    print("deployment:", dep["deployment_id"])

    with open(zip_path, "rb") as f:
        put = urllib.request.Request(dep["uploadUrl"], data=f.read(), method="PUT")
        with urllib.request.urlopen(put, timeout=120) as resp:
            assert resp.status == 200, f"upload failed: {resp.status}"
    print("zip uploaded")

    started = tool("manage_frontend",
                   {"app_id": APP, "action": "start_deployment",
                    "deployment_id": dep["deployment_id"]}, 3)
    print(f"status: {started.get('status')} -> {started.get('url')}")
    return 0 if started.get("status") == "READY" else 1


if __name__ == "__main__":
    sys.exit(main())
