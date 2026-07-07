"""Gate check: Investigator finds conflicts and recommends a runnable method.

Pass criteria (plan §5.3): header block present, conflicts listed OR explicit
none, recommended_method_id matches a Method node in the graph.
"""

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import DATABASE, get_driver

BASE = os.environ.get("P2R_BASE", "http://127.0.0.1:8787")


def main() -> int:
    req = urllib.request.Request(f"{BASE}/api/investigate", method="POST")
    with urllib.request.urlopen(req, timeout=300) as resp:
        r = json.load(resp)

    failures = []
    if not r.get("header_present"):
        failures.append("U1_header_present: no ---P2R--- block")
    if r.get("status") not in ("ok", "partial"):
        failures.append(f"U2_status_ok: status={r.get('status')}")
    payload = r.get("payload") or {}
    conflicts = payload.get("conflicts") or []
    if not conflicts and r.get("status") != "partial":
        failures.append("V1_conflicts_found: empty conflicts with status ok")
    method_id = payload.get("recommended_method_id")
    if not method_id:
        failures.append("V3_method_recommended: missing recommended_method_id")
    else:
        with get_driver() as driver:
            recs, _, _ = driver.execute_query(
                "MATCH (m:Method {id: $id}) RETURN count(m) AS c",
                id=method_id, database_=DATABASE)
            if recs[0]["c"] != 1:
                failures.append(f"V3_method_recommended: '{method_id}' not in graph")
        claim_ids = payload.get("recommended_claim_ids") or []
        with get_driver() as driver:
            recs, _, _ = driver.execute_query(
                "MATCH (c:Claim) WHERE c.id IN $ids RETURN count(c) AS c",
                ids=claim_ids, database_=DATABASE)
            if recs[0]["c"] != len(claim_ids):
                failures.append("V4_claims_linked: some recommended_claim_ids not in graph")

    print(f"investigator status={r.get('status')} method={method_id} "
          f"conflicts={len(conflicts)} untested={len(payload.get('untested_claims') or [])}")
    if failures:
        for f in failures:
            print("  ✗", f)
        return 1
    print("  ✓ V1–V4 passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
