"""Idempotent Neo4j loader: extracted paper JSON -> knowledge graph.

Nodes:  Paper, Author, Claim, Method, Dataset, Task (Run/Artifact added by curator.py)
Edges:  WROTE, CITES, FROM, DESCRIBED_IN, EVALUATED_ON, ADDRESSES,
        SUPPORTS/CONTRADICTS (claim-to-claim)

All writes use MERGE keyed on stable ids, so reloading is safe. The Aura
instance is shared with another project — deletes are restricted to OUR_LABELS.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import DATABASE, OUR_LABELS, get_driver

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTRACTED_DIR = os.path.join(ROOT, "papers", "extracted")


def load_paper(tx, data: dict):
    p = data["paper"]
    tx.run(
        """
        MERGE (paper:Paper {id: $id})
        SET paper.title = $title, paper.year = $year,
            paper.arxiv = $arxiv, paper.topic = $topic
        WITH paper
        MERGE (task:Task {id: $topic})
        SET task.name = $topic
        MERGE (paper)-[:ADDRESSES]->(task)
        """,
        id=p["id"], title=p["title"], year=p["year"],
        arxiv=p.get("arxiv"), topic=p.get("topic", "unknown"),
    )
    for author in p.get("authors", []):
        tx.run(
            """
            MERGE (a:Author {name: $name})
            WITH a MATCH (paper:Paper {id: $pid})
            MERGE (a)-[:WROTE]->(paper)
            """,
            name=author, pid=p["id"],
        )
    for claim in data.get("claims", []):
        tx.run(
            """
            MERGE (c:Claim {id: $id})
            SET c.text = $text, c.metric = $metric
            WITH c MATCH (paper:Paper {id: $pid})
            MERGE (c)-[:FROM]->(paper)
            """,
            id=claim["id"], text=claim["text"],
            metric=claim.get("metric"), pid=p["id"],
        )
    for method in data.get("methods", []):
        tx.run(
            """
            MERGE (m:Method {id: $id})
            SET m.name = $name, m.description = $description,
                m.runnable_hint = $runnable_hint, m.params = $params
            WITH m MATCH (paper:Paper {id: $pid})
            MERGE (m)-[:DESCRIBED_IN]->(paper)
            """,
            id=method["id"], name=method["name"],
            description=method["description"],
            runnable_hint=method["runnable_hint"],
            params=json.dumps(method.get("params", [])), pid=p["id"],
        )
    for ds in data.get("datasets", []):
        tx.run(
            """
            MERGE (d:Dataset {id: $id})
            SET d.name = $name
            WITH d MATCH (paper:Paper {id: $pid})
            MERGE (paper)-[:EVALUATED_ON]->(d)
            """,
            id=ds["id"], name=ds["name"], pid=p["id"],
        )
    for cited in data.get("cites", []):
        tx.run(
            """
            MATCH (a:Paper {id: $pid})
            MERGE (b:Paper {id: $cited})
            MERGE (a)-[:CITES]->(b)
            """,
            pid=p["id"], cited=cited,
        )


def load_claim_relations(tx, data: dict):
    """Second pass — claim ids may reference claims from other papers."""
    for rel in data.get("claim_relations", []):
        if rel["type"] not in ("SUPPORTS", "CONTRADICTS"):
            raise ValueError(f"bad relation type: {rel}")
        tx.run(
            f"""
            MATCH (a:Claim {{id: $from_id}}), (b:Claim {{id: $to_id}})
            MERGE (a)-[:{rel['type']}]->(b)
            """,
            from_id=rel["from"], to_id=rel["to"],
        )


def reset_our_graph(driver):
    """Delete ONLY this project's nodes. Never touches sceneshop data."""
    label_union = ":".join([])  # noqa — explicit loop below for clarity
    for label in OUR_LABELS:
        driver.execute_query(f"MATCH (n:{label}) DETACH DELETE n", database_=DATABASE)


def summary(driver) -> dict:
    counts = {}
    for label in OUR_LABELS:
        recs, _, _ = driver.execute_query(
            f"MATCH (n:{label}) RETURN count(n) AS c", database_=DATABASE
        )
        counts[label] = recs[0]["c"]
    recs, _, _ = driver.execute_query(
        """
        MATCH (a)-[r]->(b)
        WHERE any(l IN labels(a) WHERE l IN $labels)
          AND any(l IN labels(b) WHERE l IN $labels)
        RETURN type(r) AS t, count(*) AS c ORDER BY t
        """,
        labels=OUR_LABELS, database_=DATABASE,
    )
    counts["_relationships"] = {r["t"]: r["c"] for r in recs}
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reset", action="store_true",
                    help="delete this project's nodes before loading")
    args = ap.parse_args()

    files = sorted(f for f in os.listdir(EXTRACTED_DIR) if f.endswith(".json"))
    with get_driver() as driver:
        if args.reset:
            reset_our_graph(driver)
            print("reset: cleared Verigraph labels")
        datas = []
        with driver.session(database=DATABASE) as session:
            for fname in files:
                with open(os.path.join(EXTRACTED_DIR, fname)) as f:
                    data = json.load(f)
                session.execute_write(load_paper, data)
                datas.append(data)
            for data in datas:
                session.execute_write(load_claim_relations, data)
        print(f"loaded {len(files)} papers")
        print(json.dumps(summary(driver), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
