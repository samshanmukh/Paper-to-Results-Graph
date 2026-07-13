"""Recover workspace storage and rebuild Neo4j from its durable manifest."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.workspace import recover_workspace


def restore_all() -> dict:
    """Reconcile Neo4j with active papers and runs; safe and idempotent."""
    return recover_workspace()


if __name__ == "__main__":
    print(json.dumps(restore_all()))
