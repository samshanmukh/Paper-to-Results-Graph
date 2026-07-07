#!/usr/bin/env python3
"""Submit HackwithBay entry via Butterbase MCP HTTP endpoint."""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k] = v
    return out


def mcp_call(name: str, arguments: dict) -> dict:
    env = load_env()
    key = env.get("BUTTERBASE_API_KEY") or os.environ.get("BUTTERBASE_API_KEY")
    if not key:
        raise SystemExit("BUTTERBASE_API_KEY missing")

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.butterbase.ai/mcp",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        text = resp.read().decode()
    for line in text.splitlines():
        if line.startswith("data: "):
            data = json.loads(line[6:])
            if "result" in data:
                content = data["result"].get("content", [])
                if content and content[0].get("type") == "text":
                    return json.loads(content[0]["text"])
            if "error" in data:
                return {"error": data["error"]}
    return {"raw": text}


def main() -> None:
    submission_code = sys.argv[1] if len(sys.argv) > 1 else "ENJOY0707"
    prep_args: dict = {"action": "prep", "submission_code": submission_code}

    print("=== PREP ===")
    prep = mcp_call("prep_and_submit_hackathon_entry", prep_args)
    print(json.dumps(prep, indent=2))

    submit_args = {
        "action": "submit",
        "hackathon_slug": "HackwithBay-0707",
        "app_id": "app_ini4p5qvd42o",
        "data": {
            "project_title": "VeriGraph",
            "team_members_names_all": "Ali Amjad, Siddharth Reddy, Dileep Kumar Sharma, Sanmukh Sain Karri",
            "team_members_emails_all": (
                "ali.amjad52114@gmail.com, siddharth.ieeja@gmail.com, "
                "dkus2896@gmail.com, shanmukhsain@gmail.com"
            ),
            "team_members_linkedin": (
                "https://www.linkedin.com/in/ali-amjad-a80732137/, "
                "https://www.linkedin.com/in/siddharth-ieeja/, "
                "https://www.linkedin.com/in/shanmukhsain/, "
                "https://www.linkedin.com/in/dileep2896/"
            ),
            "deployed_project_url": "https://paper-to-results-graph.butterbase.dev/demo",
            "phone_number": os.environ.get("HACKATHON_PHONE", "415-000-0000"),
            "github_repo": "https://github.com/samshanmukh/paper-to-results-graph",
            "demo_presentation": (
                "Demo video: https://www.loom.com/share/638401d9e5a04a6f8e84481e99dd37cf | "
                "Slides: https://docs.google.com/presentation/d/1Sw3pdLbxbMfgNsi3Vuc72U37X4EeqaRybUe0qmlmdQ4/edit?usp=sharing | "
                "Alt deploy: https://paper2result.butterbase.dev"
            ),
            "feedback": (
                "Excellent hackathon at AWS Builder Loft. Butterbase enabled fast backend + frontend deploy, "
                "RocketRide powered our multi-step agent pipeline, and Neo4j stores verifiable evidence graphs. "
                "VeriGraph turns research papers into executable, inspectable result chains for scientists."
            ),
        },
        "submission_code": submission_code,
    }

    print("\n=== SUBMIT ===")
    result = mcp_call("prep_and_submit_hackathon_entry", submit_args)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
