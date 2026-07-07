"""Reset to pristine demo state: papers/claims/methods loaded, NO runs yet.

Deletes only Run + Artifact nodes (evidence), reloads the paper graph, and
clears local run records — so the live demo starts at 'no runs yet' and the
evidence flip happens on stage. Butterbase history is kept (it's the archive).

Usage: python scripts/reset_demo.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.demo_reset import reset_demo_state


def main() -> int:
    result = reset_demo_state()
    print("cleared Run + Artifact nodes")
    print(f"reloaded {result['papers']} papers")
    print(f"evidence table: {result['claims']} claims, "
          f"{'ALL no-runs-yet ✓' if result['pristine'] else 'STILL HAS RUNS ✗'}")
    return 0 if result["pristine"] else 1


if __name__ == "__main__":
    sys.exit(main())
