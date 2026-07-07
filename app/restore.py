"""Rebuild the graph from disk: papers/extracted/*.json + runs/*.json.

Everything the graph contains is derivable from files, so a stray write from
an LLM-generated Cypher query can always be healed. All loaders MERGE, so
restore is idempotent and safe to run at any time.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.curator import curate_run
from app.db import DATABASE, get_driver
from app.graph import EXTRACTED_DIR, load_claim_relations, load_paper

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(ROOT, "runs")


def restore_all() -> dict:
    papers = runs = 0
    with get_driver() as driver, driver.session(database=DATABASE) as session:
        datas = []
        for fname in sorted(os.listdir(EXTRACTED_DIR)):
            if not fname.endswith(".json"):
                continue
            with open(os.path.join(EXTRACTED_DIR, fname)) as f:
                data = json.load(f)
            session.execute_write(load_paper, data)
            datas.append(data)
            papers += 1
        for data in datas:
            session.execute_write(load_claim_relations, data)
        if os.path.isdir(RUNS_DIR):
            for fname in sorted(os.listdir(RUNS_DIR)):
                if not fname.endswith(".json"):
                    continue
                with open(os.path.join(RUNS_DIR, fname)) as f:
                    record = json.load(f)
                session.execute_write(curate_run, record)
                runs += 1
    return {"papers": papers, "runs": runs}


if __name__ == "__main__":
    print(json.dumps(restore_all()))
