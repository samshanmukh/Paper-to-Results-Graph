"""Gate check: Executor runs a method via the canonical /api/run endpoint.

Pass criteria (plan §5.4): header present, run_id real (exists in Neo4j —
proves the agent's HTTP call created the same Run node the UI path would),
exit 0, verdicts present, graph_updated true.
"""

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import DATABASE, GRAPH_NAMESPACE, get_driver

BASE = os.environ.get("P2R_BASE", "http://127.0.0.1:8787")
METHOD = sys.argv[1] if len(sys.argv) > 1 else "wilson2017-m1"


def main() -> int:
    req = urllib.request.Request(
        f"{BASE}/api/execute", method="POST",
        data=json.dumps({"method_id": METHOD}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        r = json.load(resp)

    failures = []
    if not r.get("header_present"):
        failures.append("U1_header_present: no ---P2R--- block")
    payload = r.get("payload") or {}
    run_id = payload.get("run_id") or ""
    if not run_id.startswith("run-"):
        failures.append(f"E1_run_id_present: bad run_id {run_id!r}")
    else:
        with get_driver() as driver:
            recs, _, _ = driver.execute_query(
                "MATCH (x:Run:Verigraph {"
                "id: $id, verigraph_namespace: $graph_namespace"
                "}) RETURN count(x) AS c",
                id=run_id, graph_namespace=GRAPH_NAMESPACE, database_=DATABASE)
            if recs[0]["c"] != 1:
                failures.append(f"E4_graph_updated: {run_id} not found in Neo4j")
    if payload.get("exit_code") != 0 or payload.get("error") not in (None, "null"):
        failures.append(f"E2_exit_success: exit={payload.get('exit_code')} error={payload.get('error')}")
    if not payload.get("claim_checks"):
        failures.append("E3_verdicts_present: no claim_checks")
    if not payload.get("metrics"):
        failures.append("E5_metrics_present: empty metrics")
    if METHOD == "wilson2017-m1" and payload.get("metrics"):
        m = payload["metrics"]
        adam = m.get("test_error_adam", m.get("adam_test_error"))
        gd = m.get("test_error_gd", m.get("gd_test_error"))
        if not (isinstance(adam, (int, float)) and adam > 0 and
                isinstance(gd, (int, float)) and gd < 0.01):
            failures.append(f"E6_wilson_demo: adam={adam} gd={gd}")

    print(f"executor status={r.get('status')} run={run_id} "
          f"backend={payload.get('backend')} verdicts={len(payload.get('claim_checks') or [])}")
    if failures:
        for f in failures:
            print("  ✗", f)
        return 1
    print("  ✓ E1–E6 passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
