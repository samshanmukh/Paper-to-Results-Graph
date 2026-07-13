"""Idempotent Neo4j loader: extracted paper JSON -> knowledge graph.

Nodes:  Paper, Author, Claim, Method, Dataset, Task (Run/Artifact added by curator.py)
Edges:  WROTE, CITES, FROM, DESCRIBED_IN, EVALUATED_ON, ADDRESSES,
        SUPPORTS/CONTRADICTS (claim-to-claim)

All writes use MERGE keyed on stable ids inside the Verigraph namespace, so
reloading is safe. The Aura instance is shared with another project; generic
domain labels are never treated as proof of ownership.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import DATABASE, GRAPH_NAMESPACE, GRAPH_OWNER_LABEL, OUR_LABELS, get_driver

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTRACTED_DIR = os.path.join(ROOT, "papers", "extracted")


def load_paper(tx, data: dict):
    p = data["paper"]
    tx.run(
        """
        MERGE (paper:Paper:Verigraph {
            id: $id, verigraph_namespace: $graph_namespace
        })
        SET paper.title = $title, paper.year = $year,
            paper.arxiv = $arxiv, paper.topic = $topic
        WITH paper
        MERGE (task:Task:Verigraph {
            id: $topic, verigraph_namespace: $graph_namespace
        })
        SET task.name = $topic
        MERGE (paper)-[:ADDRESSES]->(task)
        """,
        id=p["id"], title=p["title"], year=p["year"],
        arxiv=p.get("arxiv"), topic=p.get("topic", "unknown"),
        graph_namespace=GRAPH_NAMESPACE,
    )
    for author in p.get("authors", []):
        tx.run(
            """
            MERGE (a:Author:Verigraph {
                name: $name, verigraph_namespace: $graph_namespace
            })
            WITH a MATCH (paper:Paper:Verigraph {
                id: $pid, verigraph_namespace: $graph_namespace
            })
            MERGE (a)-[:WROTE]->(paper)
            """,
            name=author, pid=p["id"], graph_namespace=GRAPH_NAMESPACE,
        )
    for claim in data.get("claims", []):
        tx.run(
            """
            MERGE (c:Claim:Verigraph {
                id: $id, verigraph_namespace: $graph_namespace
            })
            SET c.text = $text, c.metric = $metric
            WITH c MATCH (paper:Paper:Verigraph {
                id: $pid, verigraph_namespace: $graph_namespace
            })
            MERGE (c)-[:FROM]->(paper)
            """,
            id=claim["id"], text=claim["text"],
            metric=claim.get("metric"), pid=p["id"],
            graph_namespace=GRAPH_NAMESPACE,
        )
    for method in data.get("methods", []):
        tx.run(
            """
            MERGE (m:Method:Verigraph {
                id: $id, verigraph_namespace: $graph_namespace
            })
            SET m.name = $name, m.description = $description,
                m.runnable_hint = $runnable_hint, m.params = $params
            WITH m MATCH (paper:Paper:Verigraph {
                id: $pid, verigraph_namespace: $graph_namespace
            })
            MERGE (m)-[:DESCRIBED_IN]->(paper)
            """,
            id=method["id"], name=method["name"],
            description=method["description"],
            runnable_hint=method["runnable_hint"],
            params=json.dumps(method.get("params", [])), pid=p["id"],
            graph_namespace=GRAPH_NAMESPACE,
        )
    for ds in data.get("datasets", []):
        tx.run(
            """
            MERGE (d:Dataset:Verigraph {
                id: $id, verigraph_namespace: $graph_namespace
            })
            SET d.name = $name
            WITH d MATCH (paper:Paper:Verigraph {
                id: $pid, verigraph_namespace: $graph_namespace
            })
            MERGE (paper)-[:EVALUATED_ON]->(d)
            """,
            id=ds["id"], name=ds["name"], pid=p["id"],
            graph_namespace=GRAPH_NAMESPACE,
        )
    for cited in data.get("cites", []):
        tx.run(
            """
            MATCH (a:Paper:Verigraph {
                id: $pid, verigraph_namespace: $graph_namespace
            })
            MERGE (b:Paper:Verigraph {
                id: $cited, verigraph_namespace: $graph_namespace
            })
            MERGE (a)-[:CITES]->(b)
            """,
            pid=p["id"], cited=cited, graph_namespace=GRAPH_NAMESPACE,
        )


def load_claim_relations(tx, data: dict):
    """Second pass — claim ids may reference claims from other papers."""
    for rel in data.get("claim_relations", []):
        if rel["type"] not in ("SUPPORTS", "CONTRADICTS"):
            raise ValueError(f"bad relation type: {rel}")
        tx.run(
            f"""
            MATCH (a:Claim:Verigraph {{
                id: $from_id, verigraph_namespace: $graph_namespace
            }}), (b:Claim:Verigraph {{
                id: $to_id, verigraph_namespace: $graph_namespace
            }})
            MERGE (a)-[:{rel['type']}]->(b)
            """,
            from_id=rel["from"], to_id=rel["to"],
            graph_namespace=GRAPH_NAMESPACE,
        )


def reset_our_graph(driver):
    """Delete only explicitly owned nodes in this Verigraph namespace."""
    driver.execute_query(
        f"MATCH (n:{GRAPH_OWNER_LABEL} {{verigraph_namespace: $graph_namespace}}) "
        "DETACH DELETE n",
        graph_namespace=GRAPH_NAMESPACE,
        database_=DATABASE,
    )


def summary(driver) -> dict:
    counts = {}
    for label in OUR_LABELS:
        recs, _, _ = driver.execute_query(
            f"MATCH (n:{label}:{GRAPH_OWNER_LABEL} "
            "{verigraph_namespace: $graph_namespace}) RETURN count(n) AS c",
            graph_namespace=GRAPH_NAMESPACE,
            database_=DATABASE,
        )
        counts[label] = recs[0]["c"]
    recs, _, _ = driver.execute_query(
        """
        MATCH (a:Verigraph {verigraph_namespace: $graph_namespace})-[r]->
              (b:Verigraph {verigraph_namespace: $graph_namespace})
        RETURN type(r) AS t, count(*) AS c ORDER BY t
        """,
        graph_namespace=GRAPH_NAMESPACE, database_=DATABASE,
    )
    counts["_relationships"] = {r["t"]: r["c"] for r in recs}
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reset", action="store_true",
                    help="delete this project's nodes before loading")
    args = ap.parse_args()

    from app.workspace import active_papers, recover_workspace

    datas = active_papers()
    with get_driver() as driver:
        if args.reset:
            result = recover_workspace()
            if result.get("partial") or not result.get("ok"):
                print(json.dumps(result, indent=2), file=sys.stderr)
                return 1
            print("reset: reconciled Neo4j to the active workspace manifest")
        else:
            with driver.session(database=DATABASE) as session:
                for data in datas:
                    session.execute_write(load_paper, data)
                for data in datas:
                    session.execute_write(load_claim_relations, data)
        print(f"loaded {len(datas)} active papers")
        print(json.dumps(summary(driver), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
