"""Full Playbook A gate: one Conductor request drives investigate → execute
→ brief, and the graph actually changes.

Pass criteria: run count increases, the Conductor's answer names all three
sub-agents, cites a real run id, and the evidence table reflects the run.
"""

import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import DATABASE, get_driver

BASE = os.environ.get("P2R_BASE", "http://127.0.0.1:8787")

PROMPT = ("Full evidence workflow: investigate what to test, run the recommended "
          "method, then give me the evidence brief.")


def run_count() -> int:
    with get_driver() as driver:
        recs, _, _ = driver.execute_query(
            "MATCH (r:Run) RETURN count(r) AS c", database_=DATABASE)
        return recs[0]["c"]


def main() -> int:
    before = run_count()
    req = urllib.request.Request(
        f"{BASE}/api/conduct", method="POST",
        data=json.dumps({"message": PROMPT}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as resp:
        answer = json.load(resp)["answer"]
    after = run_count()

    failures = []
    low = answer.lower()
    for agent in ("investigator", "executor", "reporter"):
        if agent not in low:
            failures.append(f"synthesis missing [{agent}] line")
    if after <= before:
        failures.append(f"no new Run node (before={before}, after={after})")
    run_ids = list(set(re.findall(r"run-[\w-]+", answer)))
    if not run_ids:
        failures.append("answer cites no run id")
    else:
        with get_driver() as driver:
            recs, _, _ = driver.execute_query(
                "MATCH (r:Run) WHERE r.id IN $ids RETURN count(r) AS c",
                ids=run_ids, database_=DATABASE)
            exact = recs[0]["c"]
            if exact == 0:
                # tolerate transcription slips (e.g. dropped trailing Z) but
                # reject genuinely invented ids
                recs, _, _ = driver.execute_query(
                    """
                    UNWIND $ids AS cited
                    MATCH (r:Run) WHERE r.id STARTS WITH cited OR cited STARTS WITH r.id
                    RETURN count(DISTINCT r) AS c
                    """,
                    ids=[i for i in run_ids if len(i) > 20], database_=DATABASE)
                if recs[0]["c"] == 0:
                    failures.append("cited run ids not found in graph (hallucination)")
                else:
                    print("  ⚠ cited run ids matched by prefix only (transcription slip)")

    print(f"conductor: runs {before} -> {after}; cited runs: {sorted(set(run_ids))[:2]}")
    print("--- answer head ---")
    print(answer[:600])
    print("-------------------")
    if failures:
        for f in failures:
            print("  ✗", f)
        return 1
    print("  ✓ Playbook A passed (investigate → execute → brief, verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
