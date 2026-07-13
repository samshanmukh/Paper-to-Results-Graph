"""Canned Cypher queries over the Verigraph knowledge graph.

Usage: python app/queries.py <claims|conflicts|methods|evidence> [--json]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import DATABASE, GRAPH_NAMESPACE, get_driver

QUERIES = {
    "claims": (
        "All claims with their source paper",
        """
        MATCH (c:Claim:Verigraph {verigraph_namespace: $graph_namespace})-[:FROM]->
              (p:Paper:Verigraph {verigraph_namespace: $graph_namespace})
        RETURN p.id AS paper, c.id AS claim, c.text AS text
        ORDER BY paper, claim
        """,
    ),
    "conflicts": (
        "Cross-paper claim conflicts",
        """
        MATCH (a:Claim:Verigraph {verigraph_namespace: $graph_namespace})-
              [:CONTRADICTS]->
              (b:Claim:Verigraph {verigraph_namespace: $graph_namespace}),
              (a)-[:FROM]->
              (pa:Paper:Verigraph {verigraph_namespace: $graph_namespace}),
              (b)-[:FROM]->
              (pb:Paper:Verigraph {verigraph_namespace: $graph_namespace})
        RETURN pa.id AS from_paper, a.text AS claim,
               pb.id AS against_paper, b.text AS contradicts
        """,
    ),
    "methods": (
        "Runnable methods and where they were described",
        """
        MATCH (m:Method:Verigraph {verigraph_namespace: $graph_namespace})-
              [:DESCRIBED_IN]->
              (p:Paper:Verigraph {verigraph_namespace: $graph_namespace})
        RETURN m.id AS method, m.name AS name, p.id AS paper,
               m.runnable_hint AS runnable_hint
        ORDER BY method
        """,
    ),
    "evidence": (
        "Which claims have executable evidence (Run nodes)?",
        """
        MATCH (c:Claim:Verigraph {verigraph_namespace: $graph_namespace})-[:FROM]->
              (p:Paper:Verigraph {verigraph_namespace: $graph_namespace})
        OPTIONAL MATCH (r:Run:Verigraph {verigraph_namespace: $graph_namespace})-
                       [v:VALIDATES|REFUTES]->(c)
        WHERE r.status = 'success'
           OR (r.status IS NULL AND r.error IS NULL AND r.exit_code = 0)
        WITH p, c, r, v
        ORDER BY r.created_at DESC, r.id DESC
        WITH p, c, head(collect(CASE WHEN r IS NULL THEN NULL ELSE {
            run_id: r.id,
            verdict: type(v),
            implementation_source: coalesce(r.implementation_source, 'unknown'),
            implementation_fingerprint: r.implementation_fingerprint,
            context_digest: r.context_digest,
            provisional: coalesce(v.provisional, false)
                OR coalesce(r.provisional, false)
                OR coalesce(r.implementation_source, 'unknown') <> 'curated'
                OR r.implementation_fingerprint IS NULL
                OR r.context_digest IS NULL
        } END)) AS latest
        RETURN p.id AS paper, c.id AS claim, c.text AS text,
               CASE WHEN latest IS NULL THEN 'no runs yet'
                    ELSE latest.verdict + ' by ' + latest.run_id END AS evidence,
               latest.run_id AS run_id, latest.verdict AS verdict,
               latest.implementation_source AS implementation_source,
               latest.implementation_fingerprint AS implementation_fingerprint,
               latest.context_digest AS context_digest,
               coalesce(latest.provisional, false) AS provisional
        ORDER BY paper, claim
        """,
    ),
}


def run_query(driver, name: str) -> list[dict]:
    _, cypher = QUERIES[name]
    records, _, _ = driver.execute_query(
        cypher, graph_namespace=GRAPH_NAMESPACE, database_=DATABASE
    )
    return [dict(r) for r in records]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("query", choices=sorted(QUERIES), nargs="?")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    names = [args.query] if args.query else sorted(QUERIES)
    with get_driver() as driver:
        for name in names:
            rows = run_query(driver, name)
            if args.json:
                print(json.dumps({name: rows}, indent=2))
            else:
                print(f"\n== {QUERIES[name][0]} ({len(rows)} rows)")
                for row in rows:
                    print("  " + " | ".join(f"{k}={v}" for k, v in row.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
