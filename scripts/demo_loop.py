"""End-to-end closed loop: method -> codegen -> run -> curate -> graph diff.

Usage: python scripts/demo_loop.py [method_id] [--backend auto|local|daytona]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.curator import curate
from app.db import DATABASE, get_driver
from app.queries import run_query
from app.runner import execute


def evidence_snapshot(driver):
    rows = run_query(driver, "evidence")
    return {row["claim"]: row["evidence"] for row in rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("method_id", nargs="?", default="wilson2017-m1")
    ap.add_argument("--backend", choices=["auto", "local", "daytona"], default="auto")
    args = ap.parse_args()

    with get_driver() as driver:
        before = evidence_snapshot(driver)

    print(f"=== closed loop for method {args.method_id} ===")
    record = execute(args.method_id, args.backend)
    curate(record)

    with get_driver() as driver:
        after = evidence_snapshot(driver)

    print("\n=== graph diff (claims whose evidence changed) ===")
    changed = {c: (before.get(c), after[c]) for c in after if before.get(c) != after[c]}
    if not changed:
        print("no evidence changes")
    for claim, (b, a) in changed.items():
        print(f"  {claim}: '{b}' -> '{a}'")

    ok = record["error"] is None and changed
    print(f"\nloop {'CLOSED ✓' if ok else 'INCOMPLETE ✗'}: "
          f"paper → method → code → {record['backend']} run → result → graph")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
