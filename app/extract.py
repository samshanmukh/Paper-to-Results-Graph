"""Extractor: paper text -> structured JSON (claims, methods, datasets, citations).

Two modes:
  --mock  : return the hand-verified golden JSON from papers/extracted/ (default
            until the RocketRide extraction pipeline lands in M6).
  live    : POST the paper text to the local RocketRide pipeline (PIPELINE_URL);
            wired up in M6.
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPERS_DIR = os.path.join(ROOT, "papers")
EXTRACTED_DIR = os.path.join(PAPERS_DIR, "extracted")

RELATION_TYPES = {"SUPPORTS", "CONTRADICTS"}


def validate_extraction(data: dict) -> list[str]:
    """Return a list of schema problems (empty list = valid)."""
    errors = []
    paper = data.get("paper")
    if not isinstance(paper, dict):
        return ["missing 'paper' object"]
    for field in ("id", "title", "authors", "year"):
        if not paper.get(field):
            errors.append(f"paper.{field} missing")

    claim_ids = set()
    for claim in data.get("claims", []):
        if not claim.get("id") or not claim.get("text"):
            errors.append(f"claim missing id/text: {claim}")
        claim_ids.add(claim.get("id"))

    for method in data.get("methods", []):
        for field in ("id", "name", "description", "runnable_hint"):
            if not method.get(field):
                errors.append(f"method {method.get('id')} missing {field}")

    for rel in data.get("claim_relations", []):
        if rel.get("type") not in RELATION_TYPES:
            errors.append(f"bad relation type: {rel}")
        if rel.get("from") not in claim_ids:
            errors.append(f"relation 'from' not a claim in this paper: {rel}")

    if not isinstance(data.get("cites", []), list):
        errors.append("'cites' must be a list of paper ids")
    return errors


def extract_mock(paper_id: str) -> dict:
    path = os.path.join(EXTRACTED_DIR, f"{paper_id}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"no golden extraction for '{paper_id}' at {path}")
    with open(path) as f:
        return json.load(f)


def extract_live(paper_id: str) -> dict:
    raise NotImplementedError(
        "Live extraction routes through the local RocketRide pipeline (M6). "
        "Use --mock until pipelines/paper2result.pipe is built."
    )


def list_papers() -> list[str]:
    return sorted(
        f[:-4] for f in os.listdir(PAPERS_DIR)
        if f.endswith(".txt") and os.path.isfile(os.path.join(PAPERS_DIR, f))
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--paper", help="paper id (basename of papers/<id>.txt); omit for all")
    ap.add_argument("--mock", action="store_true", help="use golden JSON instead of LLM")
    args = ap.parse_args()

    paper_ids = [args.paper] if args.paper else list_papers()
    failures = 0
    for pid in paper_ids:
        data = extract_mock(pid) if args.mock else extract_live(pid)
        errors = validate_extraction(data)
        if errors:
            failures += 1
            print(f"✗ {pid}: {len(errors)} schema errors")
            for e in errors:
                print(f"    - {e}")
        else:
            print(
                f"✓ {pid}: {len(data['claims'])} claims, "
                f"{len(data['methods'])} methods, "
                f"{len(data.get('datasets', []))} datasets, "
                f"cites={data.get('cites', [])}, "
                f"{len(data.get('claim_relations', []))} cross-claim relations"
            )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
