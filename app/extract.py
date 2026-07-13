"""Extractor: paper text -> structured JSON (claims, methods, datasets, citations).

Two modes:
  --mock  : validate the hand-verified golden JSON from papers/extracted/.
  live    : send paper text through the configured Butterbase-compatible LLM
            gateway (ROCKETRIDE_GATEWAY_*), then validate the untrusted result.
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app.validation import (normalize_extraction, require_paper_id,
                            validate_extraction)

PAPERS_DIR = os.path.join(ROOT, "papers")
EXTRACTED_DIR = os.path.join(PAPERS_DIR, "extracted")

RELATION_TYPES = {"SUPPORTS", "CONTRADICTS"}


def ensure_methods(data: dict) -> dict:
    """Guarantee at least one runnable method — surveys often omit them."""
    if data.get("methods"):
        return data
    pid = data["paper"]["id"]
    topic = data["paper"].get("topic", "experiment")
    claims = data.get("claims", [])
    claim_hint = claims[0]["text"][:120] if claims else "core paper finding"
    data["methods"] = [{
        "id": f"{pid}-m1",
        "name": "Toy reproduction experiment",
        "description": (
            f"Simplified numpy simulation of the paper's key claim about {topic}: "
            f"{claim_hint}"
        ),
        "runnable_hint": (
            "Build a tiny synthetic task where multiple random candidate solutions "
            "are sampled (best-of-n / inference-time scaling). Score each with a "
            "simple verifier, pick the best, and report accuracy vs n_candidates. "
            "Metric: accuracy_at_n vs n_candidates."
        ),
        "params": [
            {"name": "n_candidates", "default": 8,
             "description": "independent samples per problem (best-of-n width)"},
            {"name": "n_trials", "default": 200,
             "description": "number of synthetic problems to evaluate"},
            {"name": "noise", "default": 0.3,
             "description": "difficulty / noise level of the synthetic task"},
        ],
    }]
    return data


def extract_mock(paper_id: str) -> dict:
    paper_id = require_paper_id(paper_id)
    path = os.path.join(EXTRACTED_DIR, f"{paper_id}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"no golden extraction for '{paper_id}' at {path}")
    with open(path) as f:
        data = json.load(f)
    errors = validate_extraction(data)
    if errors:
        raise ValueError(f"golden extraction failed validation: {errors}")
    if data["paper"]["id"] != paper_id:
        raise ValueError("golden extraction paper id does not match its filename")
    return data


EXTRACTION_PROMPT = """You are the extraction stage of Verigraph.
Given a research paper's text, produce ONLY a JSON object (no prose) with:

{
  "paper": {"id": "<firstauthorYEAR, lowercase, e.g. zhang2019>",
            "title": str, "authors": [str], "year": int,
            "arxiv": str|null, "topic": "<short-kebab-topic>"},
  "claims": [{"id": "<paperid>-c<n>", "text": str, "metric": str|null}],
  "methods": [{"id": "<paperid>-m<n>", "name": str, "description": str,
               "runnable_hint": "how to reproduce as a SMALL numpy-only
                experiment printing a metric — be concrete",
               "params": [{"name": str, "default": num, "description": str}]}],
  "datasets": [{"id": "<kebab>", "name": str}],
  "cites": ["<paperid of cited papers ONLY if likely in our graph>"],
  "claim_relations": [{"from": "<this paper's claim id>",
                       "to": "<claim id, may be another paper's>",
                       "type": "SUPPORTS"|"CONTRADICTS"}]
}

Rules: 2-4 claims, AT LEAST 1 method (every paper must have a runnable toy
experiment — for surveys, design a simplified numpy simulation of the core
finding, e.g. best-of-n sampling), params are the paper's experiment parameters. Known graph papers and
their claims (link cross-paper relations against these when relevant):
adam2014 (adam2014-c1 Adam converges faster than SGD; adam2014-c2 defaults
need little tuning; adam2014-c3 update invariant to gradient rescaling),
wilson2017 (wilson2017-c1 adaptive methods generalize worse than SGD;
wilson2017-c2 SGD 0 vs Adam ~50 percent test error on separable construction;
wilson2017-c3 tuned SGD matches adaptive), adamw2017 (adamw2017-c1 L2 !=
weight decay for Adam; adamw2017-c2 AdamW closes generalization gap;
adamw2017-c3 decoupling makes weight decay independent of lr).

PAPER TEXT:
"""


def extract_live_text(text: str) -> dict:
    from app.llm import chat, extract_json_obj
    reply = chat(EXTRACTION_PROMPT + text[:24000], max_tokens=4000)
    data = extract_json_obj(reply)
    if not isinstance(data, dict):
        return normalize_extraction(data)
    data = ensure_methods(data)
    return normalize_extraction(data)


def extract_live(paper_id: str) -> dict:
    paper_id = require_paper_id(paper_id)
    with open(os.path.join(PAPERS_DIR, f"{paper_id}.txt")) as f:
        return extract_live_text(f.read())


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
