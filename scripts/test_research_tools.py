#!/usr/bin/env python3
"""Unit smoke for research_tools (compare / timeline / brief / batch plan)."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub Neo4j-backed modules so this runs without credentials.
sys.modules["app.db"] = types.ModuleType("app.db")
sys.modules["app.db"].DATABASE = "neo4j"
sys.modules["app.db"].OUR_LABELS = []
sys.modules["app.db"].get_driver = lambda: None

from app.research_tools import (  # noqa: E402
    claim_timeline,
    compare_runs,
    evidence_brief_markdown,
    methods_never_run,
)


def main() -> int:
    runs = [
        {
            "run_id": "r1",
            "method_id": "wilson2017-m1",
            "params": {"steps": 100},
            "created_at": "2026-01-01T00:00:00Z",
            "result": {
                "metrics": {"adam_test_error": 0.4},
                "claim_checks": [{"claim_id": "wilson2017-c2", "verdict": "VALIDATES", "detail": "ok"}],
            },
        },
        {
            "run_id": "r2",
            "method_id": "wilson2017-m1",
            "params": {"steps": 200},
            "created_at": "2026-01-02T00:00:00Z",
            "result": {
                "metrics": {"adam_test_error": 0.1},
                "claim_checks": [{"claim_id": "wilson2017-c2", "verdict": "REFUTES", "detail": "no"}],
            },
        },
    ]
    cmp = compare_runs("r1", "r2", runs=runs)
    assert cmp["summary"]["params_changed"] == 1
    assert cmp["summary"]["verdicts_flipped"] == 1
    tl = claim_timeline(runs=runs)
    assert tl[0]["flips"] == 1
    pending = methods_never_run(runs=runs, method_ids=["wilson2017-m1", "adam2014-m1"])
    assert pending == ["adam2014-m1"]
    md = evidence_brief_markdown(
        {
            "generated_at": "now",
            "counts": {"papers": 1, "runs": 2},
            "evidence": {"total_claims": 1, "validated": 0, "refuted": 1, "untested": 0, "coverage_pct": 100},
            "conflicts": {"total": 0, "resolved": 0, "untested": 0},
            "conflict_rows": [],
            "claim_rows": [
                {"claim": "wilson2017-c2", "paper": "wilson2017", "verdict": "REFUTES", "run_id": "r2", "detail": "no"}
            ],
        }
    )
    assert "evidence brief" in md.lower()
    assert "REFUTES" in md
    print("ok — research_tools smoke")
    return 0


if __name__ == "__main__":
    sys.exit(main())
