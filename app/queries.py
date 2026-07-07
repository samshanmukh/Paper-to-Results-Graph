"""Canned Cypher queries over the Paper2Result graph.

Usage: python app/queries.py <claims|conflicts|methods|evidence> [--json]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import DATABASE, get_driver

QUERIES = {
    "claims": (
        "All claims with their source paper",
        """
        MATCH (c:Claim)-[:FROM]->(p:Paper)
        RETURN p.id AS paper, c.id AS claim, c.text AS text
        ORDER BY paper, claim
        """,
    ),
    "conflicts": (
        "Cross-paper claim conflicts",
        """
        MATCH (a:Claim)-[:CONTRADICTS]->(b:Claim),
              (a)-[:FROM]->(pa:Paper), (b)-[:FROM]->(pb:Paper)
        RETURN pa.id AS from_paper, a.text AS claim,
               pb.id AS against_paper, b.text AS contradicts
        """,
    ),
    "methods": (
        "Runnable methods and where they were described",
        """
        MATCH (m:Method)-[:DESCRIBED_IN]->(p:Paper)
        RETURN m.id AS method, m.name AS name, p.id AS paper,
               m.runnable_hint AS runnable_hint
        ORDER BY method
        """,
    ),
    "evidence": (
        "Which claims have executable evidence (Run nodes)?",
        """
        MATCH (c:Claim)-[:FROM]->(p:Paper)
        OPTIONAL MATCH (r:Run)-[v:VALIDATES|REFUTES]->(c)
        RETURN p.id AS paper, c.id AS claim,
               CASE WHEN r IS NULL THEN 'no runs yet'
                    ELSE type(v) + ' by ' + r.id END AS evidence
        ORDER BY paper, claim
        """,
    ),
}


def run_query(driver, name: str) -> list[dict]:
    _, cypher = QUERIES[name]
    records, _, _ = driver.execute_query(cypher, database_=DATABASE)
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
