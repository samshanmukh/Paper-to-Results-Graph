"""Reset to pristine demo state: papers/claims/methods loaded, NO runs yet.

Deletes only Run + Artifact nodes (evidence), reloads the paper graph, and
clears local run records — so the live demo starts at 'no runs yet' and the
evidence flip happens on stage. Butterbase history is kept (it's the archive).

Usage: python scripts/reset_demo.py
"""

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import DATABASE, get_driver
from app.graph import EXTRACTED_DIR, load_claim_relations, load_paper
from app.queries import run_query

import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    with get_driver() as driver:
        for label in ("Run", "Artifact"):
            driver.execute_query(f"MATCH (n:{label}) DETACH DELETE n", database_=DATABASE)
        print("cleared Run + Artifact nodes")

        files = sorted(f for f in os.listdir(EXTRACTED_DIR) if f.endswith(".json"))
        datas = []
        with driver.session(database=DATABASE) as session:
            for fname in files:
                with open(os.path.join(EXTRACTED_DIR, fname)) as f:
                    data = json.load(f)
                session.execute_write(load_paper, data)
                datas.append(data)
            for data in datas:
                session.execute_write(load_claim_relations, data)
        print(f"reloaded {len(files)} papers")

        rows = run_query(driver, "evidence")
        pristine = all(r["evidence"] == "no runs yet" for r in rows)
        print(f"evidence table: {len(rows)} claims, "
              f"{'ALL no-runs-yet ✓' if pristine else 'STILL HAS RUNS ✗'}")

    for sub in ("runs", "generated"):
        path = os.path.join(ROOT, sub)
        if os.path.isdir(path):
            shutil.rmtree(path)
            print(f"cleared {sub}/")
    return 0 if pristine else 1


if __name__ == "__main__":
    sys.exit(main())
