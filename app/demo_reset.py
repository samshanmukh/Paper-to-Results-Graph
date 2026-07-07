"""Reset demo graph to pristine state (papers loaded, no runs)."""

import json
import os
import shutil

from app.db import DATABASE, get_driver
from app.graph import EXTRACTED_DIR, load_claim_relations, load_paper
from app.queries import run_query

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def reset_demo_state() -> dict:
    """Delete runs/artifacts, reload papers, clear local run dirs."""
    with get_driver() as driver:
        for label in ("Run", "Artifact"):
            driver.execute_query(f"MATCH (n:{label}) DETACH DELETE n", database_=DATABASE)

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

        rows = run_query(driver, "evidence")
        pristine = all(r["evidence"] == "no runs yet" for r in rows)

    for sub in ("runs", "generated"):
        path = os.path.join(ROOT, sub)
        if os.path.isdir(path):
            shutil.rmtree(path)

    return {
        "papers": len(files),
        "claims": len(rows),
        "pristine": pristine,
    }
