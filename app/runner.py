"""Runner: execute a materialized method implementation and capture everything.

Backends:
  daytona — isolated sandbox via Daytona SDK (needs DAYTONA_API_KEY)
  local   — subprocess fallback so the demo never blocks on the sandbox
  auto    — daytona if a key is present, else local

Every run produces a run record saved to runs/<run_id>.json:
  {run_id, method_id, backend, exit_code, stdout, stderr, duration_s,
   result: <parsed JSON contract or null>, error: <str or null>, created_at}
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import certifi

# macOS python.org builds lack system CAs (same fix as app/db.py); must be
# set before the Daytona SDK opens TLS connections.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.codegen import materialize, parse_result_line

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(ROOT, "runs")

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))


def run_local(code_path: str, timeout: int = 300) -> dict:
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, code_path], capture_output=True, text=True, timeout=timeout
    )
    return {
        "backend": "local",
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "duration_s": round(time.monotonic() - started, 2),
    }


def run_daytona(code_path: str, timeout: int = 300) -> dict:
    from daytona_sdk import Daytona, DaytonaConfig

    with open(code_path) as f:
        code = f.read()

    daytona = Daytona(DaytonaConfig(api_key=os.environ["DAYTONA_API_KEY"]))
    started = time.monotonic()
    sandbox = daytona.create()
    try:
        # default sandbox image may lack numpy; install quietly first
        sandbox.process.exec("pip install -q numpy", timeout=180)
        response = sandbox.process.code_run(code, timeout=timeout)
        return {
            "backend": "daytona",
            "sandbox_id": getattr(sandbox, "id", None),
            "exit_code": response.exit_code,
            "stdout": response.result or "",
            "stderr": "",
            "duration_s": round(time.monotonic() - started, 2),
        }
    finally:
        sandbox.delete()


def execute(method_id: str, backend: str = "auto") -> dict:
    if backend == "auto":
        backend = "daytona" if os.environ.get("DAYTONA_API_KEY") else "local"

    code_path = materialize(method_id)
    run_id = f"run-{method_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    record = {
        "run_id": run_id,
        "method_id": method_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "result": None,
        "error": None,
    }
    try:
        outcome = run_daytona(code_path) if backend == "daytona" else run_local(code_path)
        record.update(outcome)
        if record["exit_code"] == 0:
            try:
                record["result"] = parse_result_line(record["stdout"])
            except (ValueError, json.JSONDecodeError, IndexError) as e:
                record["error"] = f"output contract violation: {e}"
        else:
            record["error"] = f"non-zero exit ({record['exit_code']})"
    except Exception as e:  # sandbox/network failures are data too
        record["backend"] = backend
        record["exit_code"] = -1
        record.setdefault("stdout", "")
        record["stderr"] = str(e)
        record["duration_s"] = None
        record["error"] = f"{type(e).__name__}: {e}"

    os.makedirs(RUNS_DIR, exist_ok=True)
    path = os.path.join(RUNS_DIR, f"{run_id}.json")
    with open(path, "w") as f:
        json.dump(record, f, indent=2)
    print(f"run record: {path}")
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("method_id", help="e.g. wilson2017-m1")
    ap.add_argument("--backend", choices=["auto", "local", "daytona"], default="auto")
    args = ap.parse_args()

    record = execute(args.method_id, args.backend)
    status = "OK" if record["error"] is None else f"FAILED ({record['error']})"
    print(f"{record['run_id']} [{record['backend']}] {status} "
          f"in {record['duration_s']}s")
    if record["result"]:
        for check in record["result"]["claim_checks"]:
            print(f"  {check['verdict']} {check['claim_id']}: {check['detail']}")
    return 0 if record["error"] is None else 1


if __name__ == "__main__":
    sys.exit(main())
