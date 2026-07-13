"""Code generation and strict result-contract validation.

Curated implementations live in ``papers/impl``. LLM-generated code is
cached separately under ``generated/cache`` so its provenance cannot be lost
on a later run. Materialized filenames also carry that provenance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
import threading
from contextlib import contextmanager
from typing import Any, Iterator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
IMPL_DIR = os.path.join(ROOT, "papers", "impl")
GENERATED_DIR = os.path.join(ROOT, "generated")
CACHE_DIR = os.path.join(GENERATED_DIR, "cache")
LOCK_DIR = os.path.join(GENERATED_DIR, ".locks")

RESULT_FIELDS = frozenset({"method_id", "params", "metrics", "claim_checks"})
CHECK_FIELDS = frozenset({"claim_id", "verdict", "detail"})
VERDICTS = frozenset({"VALIDATES", "REFUTES"})
LEGACY_LLM_MARKER = "LLM-generated implementation for"
MAX_RESULT_PARAMS = 32
MAX_RESULT_CLAIMS = 64
MAX_DETAIL_CHARS = 4000
MAX_METRIC_ITEMS = 2048
MAX_METRIC_DEPTH = 8

_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.Lock] = {}


CODEGEN_PROMPT = """You are the codegen stage of Verigraph.
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


def validate_method_id(method_id: str) -> str:
    """Reject identifiers that cannot safely become filenames or graph ids."""
    from app.validation import require_method_id

    return require_method_id(method_id)


def _atomic_write(path: str, content: str) -> None:
    """Durably replace a text file without exposing partial content."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _implementation_lock(method_id: str) -> Iterator[None]:
    """Serialize generation/materialization across threads and processes."""
    validate_method_id(method_id)
    with _LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(method_id, threading.Lock())
    with thread_lock:
        os.makedirs(LOCK_DIR, exist_ok=True)
        lock_path = os.path.join(LOCK_DIR, f"{method_id}.lock")
        with open(lock_path, "a+", encoding="utf-8") as lock_file:
            try:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            except ImportError:  # pragma: no cover - Windows uses the thread lock.
                fcntl = None
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _method_context(method_id: str) -> dict[str, Any]:
    """Pull a method and claims from the active workspace manifest."""
    validate_method_id(method_id)
    from app.workspace import active_papers, get_workspace_store

    source_by_paper = {
        entry["id"]: dict(entry.get("source") or {})
        for entry in get_workspace_store().active_entries()
    }

    matches: list[dict[str, Any]] = []
    for extraction in active_papers():
        paper = extraction.get("paper") or {}
        paper_id = paper.get("id")
        source = source_by_paper.get(paper_id) or {}
        source_kind = source.get("kind")
        if source_kind not in {"tracked", "bundled", "runtime"}:
            raise RuntimeError(f"paper '{paper_id}' has no active workspace source")
        claims = extraction.get("claims") or []
        for method in extraction.get("methods") or []:
            if isinstance(method, dict) and method.get("id") == method_id:
                matches.append(
                    {
                        **method,
                        "claims": claims,
                        "paper": paper,
                        "extraction": extraction,
                        "workspace_source": source,
                        "workspace_source_kind": source_kind,
                    }
                )
    if not matches:
        raise NotImplementedError(f"method '{method_id}' is not in the active workspace")
    if len(matches) != 1:
        raise ValueError(f"method '{method_id}' is duplicated in the active workspace")
    return matches[0]


def _graph_method_claim_ids(method_id: str) -> frozenset[str]:
    """Verify the active method's claim ownership in the graph."""
    from app.db import DATABASE, GRAPH_NAMESPACE, get_driver

    with get_driver() as driver:
        recs, _, _ = driver.execute_query(
            """
            MATCH (m:Method:Verigraph {
                id: $id, verigraph_namespace: $graph_namespace
            })-[:DESCRIBED_IN]->(p:Paper:Verigraph {
                verigraph_namespace: $graph_namespace
            })
            OPTIONAL MATCH (c:Claim:Verigraph {
                verigraph_namespace: $graph_namespace
            })-[:FROM]->(p)
            RETURN m.id AS method_id, collect(DISTINCT c.id) AS claim_ids
            """,
            id=method_id,
            graph_namespace=GRAPH_NAMESPACE,
            database_=DATABASE,
        )
    if len(recs) != 1 or recs[0].get("method_id") != method_id:
        raise NotImplementedError(
            f"method '{method_id}' is missing from the graph - recover the workspace"
        )
    return frozenset(
        claim_id
        for claim_id in (recs[0].get("claim_ids") or [])
        if isinstance(claim_id, str) and claim_id
    )


def method_contract(method_id: str) -> tuple[frozenset[str], list[dict[str, Any]]]:
    """Return graph-verified allowed claims and normalized parameter metadata."""
    from app.validation import normalize_parameter_definitions

    ctx = _method_context(method_id)
    active_claims = frozenset(
        claim["id"]
        for claim in (ctx.get("claims") or [])
        if isinstance(claim, dict) and isinstance(claim.get("id"), str)
    )
    graph_claims = _graph_method_claim_ids(method_id)
    if active_claims != graph_claims:
        raise RuntimeError(
            f"method '{method_id}' claim contract differs between workspace and graph"
        )
    definitions = normalize_parameter_definitions(ctx.get("params"))
    return active_claims, definitions


def method_claim_ids(method_id: str) -> frozenset[str]:
    """Return the only claim ids an implementation may adjudicate."""
    claim_ids, _ = method_contract(method_id)
    return claim_ids


def method_workspace_source(method_id: str) -> str:
    """Return tracked, bundled, or runtime provenance for the active method."""
    return str(_method_context(method_id)["workspace_source_kind"])


def _digest_json(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _context_digest(context: dict[str, Any]) -> str:
    """Fingerprint every active paper field that can define an implementation."""
    return _digest_json(
        {
            "schema": 1,
            "extraction": context["extraction"],
            "workspace_source": context["workspace_source"],
        }
    )


def method_context_digest(method_id: str) -> str:
    """Return the immutable cache key for the active method/paper revision."""
    return _context_digest(_method_context(method_id))


def implementation_fingerprint(code: str) -> str:
    """Hash the exact implementation bytes that will be executed."""
    if not isinstance(code, str):
        raise TypeError("implementation code must be text")
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def generate_implementation(
    method_id: str,
    *,
    context: dict[str, Any] | None = None,
) -> str:
    """LLM-generate an implementation via the Butterbase gateway."""
    from app.llm import chat, extract_code_block

    ctx = context or _method_context(method_id)
    prompt = CODEGEN_PROMPT.format(
        method_id=method_id,
        name=ctx["name"],
        description=ctx["description"],
        runnable_hint=ctx["runnable_hint"],
        params=ctx.get("params") or "[]",
        claims=json.dumps(
            [claim for claim in (ctx.get("claims") or []) if claim.get("id")],
            indent=1,
        ),
    )
    code = extract_code_block(chat(prompt, max_tokens=6000))
    if not isinstance(code, str) or not code.strip():
        raise ValueError("code generation returned an empty implementation")
    return code


def _get_implementation_unlocked(
    method_id: str,
    *,
    allow_curated: bool = True,
    context: dict[str, Any] | None = None,
) -> tuple[str, str, str]:
    context = context or _method_context(method_id)
    context_digest = _context_digest(context)
    curated_path = os.path.join(IMPL_DIR, f"{method_id}.py")
    cache_path = os.path.join(CACHE_DIR, method_id, f"{context_digest}.py")

    if allow_curated and os.path.isfile(curated_path):
        with open(curated_path, encoding="utf-8") as handle:
            code = handle.read()
        # Older versions cached generated code in papers/impl. Preserve the
        # file for audit, but its original paper context is unknowable. Never
        # assign it today's digest or execute it as a current implementation.
        if LEGACY_LLM_MARKER not in code[:512]:
            return "curated", code, context_digest

    if os.path.isfile(cache_path):
        with open(cache_path, encoding="utf-8") as handle:
            return "llm", handle.read(), context_digest

    code = generate_implementation(method_id, context=context)
    _atomic_write(cache_path, code)
    return "llm", code, context_digest


def get_implementation(
    method_id: str,
    *,
    allow_curated: bool = True,
) -> tuple[str, str]:
    """Return ``(source, code)`` while retaining generated provenance."""
    with _implementation_lock(method_id):
        source, code, _ = _get_implementation_unlocked(
            method_id,
            allow_curated=allow_curated,
        )
        return source, code


def implementation_source(method_id: str, *, allow_curated: bool = True) -> str:
    """Classify an implementation without triggering LLM generation."""
    with _implementation_lock(method_id):
        curated_path = os.path.join(IMPL_DIR, f"{method_id}.py")
        if allow_curated and os.path.isfile(curated_path):
            with open(curated_path, encoding="utf-8") as handle:
                if LEGACY_LLM_MARKER not in handle.read(512):
                    return "curated"
        return "llm"


def implementation_metadata(
    method_id: str,
    *,
    allow_curated: bool = True,
) -> tuple[str, str | None, str]:
    """Classify available code without generating it.

    The fingerprint is ``None`` only when a generated implementation has not
    yet been materialized for the current context digest.
    """
    with _implementation_lock(method_id):
        context = _method_context(method_id)
        context_digest = _context_digest(context)
        curated_path = os.path.join(IMPL_DIR, f"{method_id}.py")
        if allow_curated and os.path.isfile(curated_path):
            with open(curated_path, encoding="utf-8") as handle:
                code = handle.read()
            if LEGACY_LLM_MARKER not in code[:512]:
                return "curated", implementation_fingerprint(code), context_digest
        cache_path = os.path.join(CACHE_DIR, method_id, f"{context_digest}.py")
        if os.path.isfile(cache_path):
            with open(cache_path, encoding="utf-8") as handle:
                return "llm", implementation_fingerprint(handle.read()), context_digest
        return "llm", None, context_digest


def materialize_with_provenance(
    method_id: str,
    *,
    allow_curated: bool = True,
) -> tuple[str, str, str, str]:
    """Materialize code and return path, source, code hash, and context hash."""
    with _implementation_lock(method_id):
        context = _method_context(method_id)
        source, code, context_digest = _get_implementation_unlocked(
            method_id,
            allow_curated=allow_curated,
            context=context,
        )
        fingerprint = implementation_fingerprint(code)
        path = os.path.join(
            GENERATED_DIR,
            f"{method_id}.{source}.{context_digest[:16]}.{fingerprint[:16]}.py",
        )
        _atomic_write(path, code)
    print(
        f"codegen: {method_id} -> {path} "
        f"(source={source}, context={context_digest[:12]}, code={fingerprint[:12]})"
    )
    return path, source, fingerprint, context_digest


def materialize_with_source(
    method_id: str,
    *,
    allow_curated: bool = True,
) -> tuple[str, str]:
    """Compatibility wrapper returning only ``(path, source)``."""
    path, source, _, _ = materialize_with_provenance(
        method_id,
        allow_curated=allow_curated,
    )
    return path, source


def materialize(method_id: str) -> str:
    """Compatibility wrapper returning only the materialized path."""
    allow_curated = method_workspace_source(method_id) in {"tracked", "bundled"}
    path, _ = materialize_with_source(method_id, allow_curated=allow_curated)
    return path


def prune_implementation_cache(*, dry_run: bool = False) -> list[str]:
    """Remove generated cache entries that do not match an active context.

    This is an explicit maintenance operation because cached implementations
    can be regenerated and are intentionally not part of workspace storage.
    """
    from app.workspace import active_papers

    active: set[str] = set()
    for extraction in active_papers():
        for method in extraction.get("methods") or []:
            method_id = method.get("id") if isinstance(method, dict) else None
            if isinstance(method_id, str):
                digest = method_context_digest(method_id)
                active.add(os.path.abspath(os.path.join(CACHE_DIR, method_id, f"{digest}.py")))

    stale: list[str] = []
    if not os.path.isdir(CACHE_DIR):
        return stale
    for directory, _, filenames in os.walk(CACHE_DIR):
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = os.path.abspath(os.path.join(directory, filename))
            if path not in active:
                stale.append(path)
    if not dry_run:
        for path in stale:
            os.unlink(path)
        for directory, _, _ in os.walk(CACHE_DIR, topdown=False):
            if directory != CACHE_DIR:
                try:
                    os.rmdir(directory)
                except OSError:
                    pass
    return sorted(stale)


def _validate_parameter_values(params: dict[str, Any]) -> None:
    from app.validation import require_parameter_name

    if len(params) > MAX_RESULT_PARAMS:
        raise ValueError(f"result.params may contain at most {MAX_RESULT_PARAMS} values")
    for key, value in params.items():
        require_parameter_name(key, field="result.params key")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"result.params.{key} must be numeric")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"result.params.{key} must not be NaN or infinity")


def _validate_metric_value(value: Any, path: str, *, depth: int = 0) -> None:
    if depth > MAX_METRIC_DEPTH:
        raise ValueError(f"{path} exceeds maximum nesting depth {MAX_METRIC_DEPTH}")
    if isinstance(value, bool) or value is None or isinstance(value, str):
        raise ValueError(f"{path} must contain numeric metric values")
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain NaN or infinity")
        return
    if isinstance(value, list):
        if not value:
            raise ValueError(f"{path} metric arrays must not be empty")
        if len(value) > MAX_METRIC_ITEMS:
            raise ValueError(f"{path} metric array is too large")
        for index, item in enumerate(value):
            _validate_metric_value(item, f"{path}[{index}]", depth=depth + 1)
        return
    if isinstance(value, dict):
        if not value:
            raise ValueError(f"{path} metric objects must not be empty")
        if len(value) > MAX_METRIC_ITEMS:
            raise ValueError(f"{path} metric object is too large")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise ValueError(f"{path} keys must be non-empty strings")
            _validate_metric_value(item, f"{path}.{key}", depth=depth + 1)
        return
    raise ValueError(f"{path} contains unsupported metric type {type(value).__name__}")


def validate_result_contract(
    result: Any,
    *,
    expected_method_id: str | None = None,
    expected_params: dict[str, int | float] | None = None,
    allowed_claim_ids: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Validate the complete machine-readable experiment result."""
    if not isinstance(result, dict):
        raise ValueError("run output must be a JSON object")
    fields = set(result)
    missing = RESULT_FIELDS - fields
    unexpected = fields - RESULT_FIELDS
    if missing:
        raise ValueError(f"run output missing fields: {', '.join(sorted(missing))}")
    if unexpected:
        raise ValueError(f"run output has unexpected fields: {', '.join(sorted(unexpected))}")

    method_id = result["method_id"]
    if not isinstance(method_id, str) or not method_id:
        raise ValueError("result.method_id must be a non-empty string")
    if expected_method_id is not None and method_id != expected_method_id:
        raise ValueError(
            f"result.method_id '{method_id}' does not match expected '{expected_method_id}'"
        )
    validate_method_id(method_id)

    params = result["params"]
    metrics = result["metrics"]
    checks = result["claim_checks"]
    if not isinstance(params, dict):
        raise ValueError("result.params must be an object")
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError("result.metrics must be a non-empty object")
    if not isinstance(checks, list) or not checks:
        raise ValueError("result.claim_checks must be a non-empty array")
    if len(checks) > MAX_RESULT_CLAIMS:
        raise ValueError(
            f"result.claim_checks may contain at most {MAX_RESULT_CLAIMS} checks"
        )
    _validate_parameter_values(params)
    _validate_metric_value(metrics, "result.metrics")
    for name, expected_value in (expected_params or {}).items():
        if name not in params:
            raise ValueError(f"result.params is missing requested parameter '{name}'")
        if params[name] != expected_value:
            raise ValueError(
                f"result.params.{name} does not match requested value {expected_value}"
            )

    allowed = set(allowed_claim_ids) if allowed_claim_ids is not None else None
    seen: set[str] = set()
    from app.validation import require_claim_id

    for index, check in enumerate(checks):
        path = f"result.claim_checks[{index}]"
        if not isinstance(check, dict):
            raise ValueError(f"{path} must be an object")
        check_fields = set(check)
        if check_fields != CHECK_FIELDS:
            missing_check = CHECK_FIELDS - check_fields
            extra_check = check_fields - CHECK_FIELDS
            detail = []
            if missing_check:
                detail.append(f"missing {', '.join(sorted(missing_check))}")
            if extra_check:
                detail.append(f"unexpected {', '.join(sorted(extra_check))}")
            raise ValueError(f"{path} has invalid fields ({'; '.join(detail)})")
        claim_id = check["claim_id"]
        verdict = check["verdict"]
        detail = check["detail"]
        if not isinstance(claim_id, str) or not claim_id:
            raise ValueError(f"{path}.claim_id must be a non-empty string")
        require_claim_id(claim_id, field=f"{path}.claim_id")
        if claim_id in seen:
            raise ValueError(f"duplicate claim check for '{claim_id}'")
        seen.add(claim_id)
        if allowed is not None and claim_id not in allowed:
            raise ValueError(f"claim '{claim_id}' is not associated with method '{method_id}'")
        if verdict not in VERDICTS:
            raise ValueError(f"{path}.verdict must be VALIDATES or REFUTES")
        if not isinstance(detail, str) or not detail.strip():
            raise ValueError(f"{path}.detail must be a non-empty string")
        if len(detail) > MAX_DETAIL_CHARS:
            raise ValueError(f"{path}.detail exceeds {MAX_DETAIL_CHARS} characters")
    return result


def parse_result_line(
    stdout: str,
    *,
    expected_method_id: str | None = None,
    expected_params: dict[str, int | float] | None = None,
    allowed_claim_ids: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Parse and validate the final stdout line as the result contract."""
    lines = stdout.strip().splitlines()
    if not lines:
        raise ValueError("run output is empty")

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number is not allowed: {value}")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise ValueError(f"duplicate JSON key is not allowed: {key}")
            output[key] = value
        return output

    result = json.loads(
        lines[-1],
        parse_constant=reject_constant,
        object_pairs_hook=unique_object,
    )
    return validate_result_contract(
        result,
        expected_method_id=expected_method_id,
        expected_params=expected_params,
        allowed_claim_ids=allowed_claim_ids,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("method_id", nargs="?", help="e.g. wilson2017-m1")
    parser.add_argument("--run", action="store_true", help="execute through the hardened runner")
    parser.add_argument(
        "--prune-cache",
        action="store_true",
        help="remove generated implementations for inactive paper contexts",
    )
    args = parser.parse_args()

    if args.prune_cache:
        removed = prune_implementation_cache()
        print(f"pruned {len(removed)} stale implementation cache file(s)")
        return 0
    if not args.method_id:
        parser.error("method_id is required unless --prune-cache is used")

    if not args.run:
        materialize(args.method_id)
        return 0

    from app.runner import execute

    record = execute(args.method_id, "auto")
    if record.get("error"):
        print(record["error"], file=sys.stderr)
        return 1
    print("contract ok:", json.dumps(record["result"]["claim_checks"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
