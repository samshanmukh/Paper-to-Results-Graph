#!/usr/bin/env python3
"""Smoke-test Cognee Cloud connectivity (requires COGNEE_CLOUD + API key in .env)."""

from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

from app.cognee_memory import dataset_name, is_cloud_mode, is_enabled, recall


async def main() -> int:
    if not is_enabled():
        print("Cognee not enabled — set COGNEE_ENABLED=true", file=sys.stderr)
        return 1
    if not is_cloud_mode():
        print("Not in cloud mode — set COGNEE_CLOUD=true + COGNEE_SERVICE_URL + COGNEE_API_KEY", file=sys.stderr)
        return 1

    print(f"Connecting to Cognee Cloud (dataset={dataset_name()})…")
    hits = await recall("optimizer Adam Wilson", top_k=3)
    if hits:
        print(f"✓ recall returned {len(hits)} hit(s)")
        print(hits[0][:200])
    else:
        print("✓ connected (no hits yet — run scripts/sync_cognee.py to index papers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
