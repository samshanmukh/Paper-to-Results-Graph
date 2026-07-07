"""Curator: write a run record back into the knowledge graph.

Creates:
  (run:Run {id, backend, exit_code, duration_s, created_at, status})
  (run)-[:IMPLEMENTS]->(m:Method)
  (run)-[:VALIDATES|REFUTES {detail}]->(c:Claim)   per claim check
  (a:Artifact {id, kind, content})<-[:PRODUCED]-(run)  stdout log artifact

Failed runs are curated too — a failure with its error is evidence.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import DATABASE, get_driver

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(ROOT, "runs")

MAX_LOG_CHARS = 4000  # keep stdout artifacts graph-friendly


def curate_run(tx, record: dict):
    status = "success" if record.get("error") is None else "failure"
    tx.run(
        """
        MERGE (r:Run {id: $run_id})
        SET r.backend = $backend, r.exit_code = $exit_code,
            r.duration_s = $duration_s, r.created_at = $created_at,
            r.status = $status, r.error = $error,
            r.metrics = $metrics
        WITH r
        MATCH (m:Method {id: $method_id})
        MERGE (r)-[:IMPLEMENTS]->(m)
        """,
        run_id=record["run_id"], backend=record.get("backend"),
        exit_code=record.get("exit_code"), duration_s=record.get("duration_s"),
        created_at=record.get("created_at"), status=status,
        error=record.get("error"),
        metrics=json.dumps((record.get("result") or {}).get("metrics", {})),
        method_id=record["method_id"],
    )
    tx.run(
        """
        MATCH (r:Run {id: $run_id})
        MERGE (a:Artifact {id: $artifact_id})
        SET a.kind = 'stdout', a.content = $content
        MERGE (r)-[:PRODUCED]->(a)
        """,
        run_id=record["run_id"],
        artifact_id=f"{record['run_id']}-stdout",
        content=(record.get("stdout") or record.get("stderr") or "")[:MAX_LOG_CHARS],
    )
    for check in (record.get("result") or {}).get("claim_checks", []):
        if check["verdict"] not in ("VALIDATES", "REFUTES"):
            raise ValueError(f"bad verdict: {check}")
        tx.run(
            f"""
            MATCH (r:Run {{id: $run_id}}), (c:Claim {{id: $claim_id}})
            MERGE (r)-[v:{check['verdict']}]->(c)
            SET v.detail = $detail
            """,
            run_id=record["run_id"], claim_id=check["claim_id"],
            detail=check.get("detail", ""),
        )


def curate(record: dict) -> None:
    with get_driver() as driver, driver.session(database=DATABASE) as session:
        session.execute_write(curate_run, record)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_json", help="path to runs/<run_id>.json (or a run_id)")
    args = ap.parse_args()

    path = args.run_json
    if not os.path.exists(path):
        path = os.path.join(RUNS_DIR, f"{args.run_json}.json")
    with open(path) as f:
        record = json.load(f)
    curate(record)
    checks = (record.get("result") or {}).get("claim_checks", [])
    print(f"curated {record['run_id']} -> graph "
          f"({len(checks)} claim links, status={'success' if record.get('error') is None else 'failure'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
