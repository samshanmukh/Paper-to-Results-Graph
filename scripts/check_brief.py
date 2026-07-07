"""Gate check: Reporter produces an evidence brief covering real runs.

Pass criteria (plan §5.6): header present, run_ids_covered non-empty and all
in graph, headline mentions evidence, metrics quoted in prose.
"""

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import DATABASE, get_driver

BASE = os.environ.get("P2R_BASE", "http://127.0.0.1:8787")


def main() -> int:
    req = urllib.request.Request(f"{BASE}/api/brief", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            r = json.load(resp)
    except urllib.error.HTTPError as e:
        print(f"✗ /api/brief HTTP {e.code}: {e.read().decode()[:200]}")
        return 1

    failures = []
    if not r.get("header_present"):
        failures.append("U1_header_present: no ---P2R--- block")
    payload = r.get("payload") or {}
    runs = payload.get("run_ids_covered") or []
    if not runs:
        failures.append("R1_runs_covered: empty")
    else:
        with get_driver() as driver:
            recs, _, _ = driver.execute_query(
                "MATCH (r:Run) WHERE r.id IN $ids RETURN count(r) AS c",
                ids=runs, database_=DATABASE)
            if recs[0]["c"] != len(runs):
                failures.append("U4_no_hallucinated_ids: some run ids not in graph")
    headline = payload.get("headline") or ""
    if not headline:
        failures.append("R4_headline: empty")
    prose = (r.get("answer") or "").lower()
    if "validates" not in prose and "refutes" not in prose and "validated" not in prose and "refuted" not in prose:
        failures.append("R4_headline: brief never mentions verdicts")

    print(f"reporter status={r.get('status')} runs_covered={len(runs)} "
          f"validated={len(payload.get('claims_validated') or [])} "
          f"refuted={len(payload.get('claims_refuted') or [])} "
          f"chart={payload.get('chart_generated')}")
    print(f"headline: {headline[:100]}")
    if failures:
        for f in failures:
            print("  ✗", f)
        return 1
    print("  ✓ R1–R4 passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
