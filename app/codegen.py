"""Codegen: Method node -> runnable single-file Python implementation.

Sources, in order:
  1. Curated implementation in papers/impl/<method_id>.py (known-good,
     used for the demo and as the LLM few-shot reference).
  2. Live generation through the local RocketRide pipeline — lands in M6.

Generated/collected code follows one contract: last stdout line is a JSON
object {method_id, metrics{}, claim_checks[{claim_id, verdict, detail}]}.
"""

import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
IMPL_DIR = os.path.join(ROOT, "papers", "impl")
GENERATED_DIR = os.path.join(ROOT, "generated")


CODEGEN_PROMPT = """You are the codegen stage of Paper-to-Results Graph.
Write ONE self-contained Python file (numpy + stdlib ONLY, no downloads, no
network, finishes in under 60 seconds) that reproduces this method from a
research paper as a small experiment.

METHOD: {name} ({method_id})
DESCRIPTION: {description}
HOW TO REPRODUCE: {runnable_hint}
EXPERIMENT PARAMETERS (read each from env var P2R_<NAME_UPPERCASE>, with
these defaults): {params}
CLAIMS TO CHECK (decide VALIDATES or REFUTES from your measured metrics):
{claims}

HARD REQUIREMENTS:
- numpy only; np.random.default_rng(0) for reproducibility
- read every experiment parameter via os.environ.get("P2R_<NAME>", default)
- print progress lines, then AS THE VERY LAST STDOUT LINE print exactly one
  JSON object: {{"method_id": "{method_id}", "params": {{...used params...}},
  "metrics": {{...}}, "claim_checks": [{{"claim_id": str,
  "verdict": "VALIDATES"|"REFUTES", "detail": str}}]}}
- claim_checks verdicts MUST be computed from the measured metrics with an
  explicit threshold, not hardcoded.

Reply with ONLY a ```python code block."""


def _method_context(method_id: str) -> dict:
    """Pull method + its paper's claims from Neo4j for the codegen prompt."""
    from app.db import DATABASE, get_driver
    with get_driver() as driver:
        recs, _, _ = driver.execute_query(
            """
            MATCH (m:Method {id: $id})-[:DESCRIBED_IN]->(p:Paper)
            OPTIONAL MATCH (c:Claim)-[:FROM]->(p)
            RETURN m.name AS name, m.description AS description,
                   m.runnable_hint AS runnable_hint, m.params AS params,
                   collect({id: c.id, text: c.text}) AS claims
            """,
            id=method_id, database_=DATABASE,
        )
    if not recs:
        raise NotImplementedError(
            f"method '{method_id}' not found in the graph — "
            "re-upload the paper or check that extraction produced methods"
        )
    return dict(recs[0])


def generate_implementation(method_id: str) -> str:
    """LLM-generate an implementation via the Butterbase gateway."""
    from app.llm import chat, extract_code_block
    ctx = _method_context(method_id)
    prompt = CODEGEN_PROMPT.format(
        method_id=method_id, name=ctx["name"], description=ctx["description"],
        runnable_hint=ctx["runnable_hint"], params=ctx.get("params") or "[]",
        claims=json.dumps([c for c in ctx["claims"] if c.get("id")], indent=1),
    )
    return extract_code_block(chat(prompt, max_tokens=6000))


def get_implementation(method_id: str) -> tuple[str, str]:
    """Return (source, code). Curated first; else live LLM codegen (cached)."""
    curated = os.path.join(IMPL_DIR, f"{method_id}.py")
    if os.path.exists(curated):
        with open(curated) as f:
            return "curated", f.read()
    code = generate_implementation(method_id)
    os.makedirs(IMPL_DIR, exist_ok=True)
    with open(curated, "w") as f:  # cache so re-runs are stable + editable
        f.write(f'"""LLM-generated implementation for {method_id} '
                f'(Butterbase gateway). Review before trusting."""\n' + code)
    return "llm", code


def materialize(method_id: str) -> str:
    """Write the implementation into generated/ and return its path."""
    source, code = get_implementation(method_id)
    os.makedirs(GENERATED_DIR, exist_ok=True)
    path = os.path.join(GENERATED_DIR, f"{method_id}.py")
    with open(path, "w") as f:
        f.write(code)
    print(f"codegen: {method_id} -> {path} (source={source})")
    return path


def parse_result_line(stdout: str) -> dict:
    """Extract and validate the JSON contract from a run's stdout."""
    last = stdout.strip().splitlines()[-1]
    result = json.loads(last)
    for field in ("method_id", "metrics", "claim_checks"):
        if field not in result:
            raise ValueError(f"run output missing '{field}': {last[:200]}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("method_id", help="e.g. wilson2017-m1")
    ap.add_argument("--run", action="store_true",
                    help="also execute locally and validate the output contract")
    args = ap.parse_args()

    path = materialize(args.method_id)
    if args.run:
        proc = subprocess.run(
            [sys.executable, path], capture_output=True, text=True, timeout=300
        )
        if proc.returncode != 0:
            print(proc.stderr, file=sys.stderr)
            return proc.returncode
        result = parse_result_line(proc.stdout)
        print("contract ok:", json.dumps(result["claim_checks"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
