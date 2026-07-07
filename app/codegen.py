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
IMPL_DIR = os.path.join(ROOT, "papers", "impl")
GENERATED_DIR = os.path.join(ROOT, "generated")


def get_implementation(method_id: str) -> tuple[str, str]:
    """Return (source, code). source is 'curated' or 'llm'."""
    curated = os.path.join(IMPL_DIR, f"{method_id}.py")
    if os.path.exists(curated):
        with open(curated) as f:
            return "curated", f.read()
    raise NotImplementedError(
        f"No curated implementation for '{method_id}' and live LLM codegen "
        "routes through the local RocketRide pipeline (M6)."
    )


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
