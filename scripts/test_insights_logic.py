#!/usr/bin/env python3
"""Smoke-test adjudicate helpers without requiring Neo4j credentials.

Usage: python scripts/test_insights_logic.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import only the pure helper by reusing the module after stubbing db.
import types

stub = types.ModuleType("app.db")
stub.DATABASE = "neo4j"
stub.OUR_LABELS = ["Paper", "Claim", "Method", "Run", "Artifact"]
stub.get_driver = lambda: None
sys.modules["app.db"] = stub

from app.insights import _adjudicate  # noqa: E402


CASES = [
    ({"from_verdict": None, "to_verdict": None}, "untested"),
    ({"from_verdict": "VALIDATES", "to_verdict": None}, "from_leading"),
    ({"from_verdict": None, "to_verdict": "VALIDATES"}, "to_leading"),
    ({"from_verdict": "VALIDATES", "to_verdict": "REFUTES"}, "from_wins"),
    ({"from_verdict": "REFUTES", "to_verdict": "VALIDATES"}, "to_wins"),
    ({"from_verdict": "VALIDATES", "to_verdict": "VALIDATES"}, "both_supported"),
    ({"from_verdict": "REFUTES", "to_verdict": "REFUTES"}, "both_refuted"),
]


def main() -> int:
    base = {
        "from_claim": "a",
        "to_claim": "b",
        "from_paper": "p1",
        "to_paper": "p2",
        "from_text": "claim a",
        "to_text": "claim b",
    }
    for extra, expected in CASES:
        got = _adjudicate({**base, **extra})["status"]
        assert got == expected, f"expected {expected}, got {got} for {extra}"
    print(f"ok — {len(CASES)} adjudication cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
