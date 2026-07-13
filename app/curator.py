"""Validate a run record and atomically curate it into Neo4j."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.codegen import validate_method_id, validate_result_contract
from app.db import DATABASE, GRAPH_NAMESPACE, get_driver

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(ROOT, "runs")
MAX_LOG_CHARS = 4000
VALID_BACKENDS = frozenset({"local", "daytona"})
LEGACY_RUN_ID_RE = re.compile(r"^run-[a-z0-9-]+-\d{8}T\d{6}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _record_value(row: Any, key: str, default: Any = None) -> Any:
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, TypeError):
        return default


def validate_run_record(record: Any) -> dict[str, Any]:
    """Validate record-level invariants before opening a graph transaction."""
    if not isinstance(record, dict):
        raise ValueError("run record must be an object")
    # Two pre-hardening demo runs omitted params from the output contract.
    # Normalize only the unmistakable legacy id/provenance shape; all new
    # records remain subject to the complete strict schema.
    legacy_result = record.get("result")
    is_legacy_result = (
        isinstance(record.get("run_id"), str)
        and LEGACY_RUN_ID_RE.fullmatch(record["run_id"])
        and "implementation_source" not in record
        and "workspace_revision" not in record
    )
    if is_legacy_result:
        from app.validation import normalize_parameter_overrides

        legacy_params = normalize_parameter_overrides(record.get("params") or {})
        record = {
            **record,
            "params": legacy_params,
        }
        if isinstance(legacy_result, dict) and "params" not in legacy_result:
            record["result"] = {**legacy_result, "params": legacy_params}
    run_id = record.get("run_id")
    if not isinstance(run_id, str) or not run_id or len(run_id) > 255:
        raise ValueError("run_id must be a non-empty string of at most 255 characters")
    method_id = validate_method_id(record.get("method_id"))
    backend = record.get("backend")
    if backend not in VALID_BACKENDS:
        raise ValueError("run backend must be local or daytona")
    raw_source = record.get("implementation_source")
    if raw_source in {"curated", "llm"}:
        implementation_source = raw_source
    elif raw_source in {None, "unknown"}:
        implementation_source = "unknown"
    else:
        raise ValueError("implementation_source must be curated, llm, or unknown")

    implementation_fingerprint = record.get("implementation_fingerprint")
    if implementation_fingerprint is not None and not (
        isinstance(implementation_fingerprint, str)
        and SHA256_RE.fullmatch(implementation_fingerprint)
    ):
        raise ValueError("implementation_fingerprint must be a SHA-256 hex digest")
    context_digest = record.get("context_digest")
    if context_digest is not None and not (
        isinstance(context_digest, str) and SHA256_RE.fullmatch(context_digest)
    ):
        raise ValueError("context_digest must be a SHA-256 hex digest")
    explicit_provisional = record.get("provisional")
    if explicit_provisional is not None and not isinstance(explicit_provisional, bool):
        raise ValueError("provisional must be a boolean")
    provisional = bool(explicit_provisional) or (
        implementation_source != "curated"
        or implementation_fingerprint is None
        or context_digest is None
    )
    record = {
        **record,
        "implementation_source": implementation_source,
        "implementation_fingerprint": implementation_fingerprint,
        "context_digest": context_digest,
        "provisional": provisional,
    }
    if implementation_source == "llm" and backend == "local":
        raise ValueError("LLM-generated implementations cannot be curated as local runs")
    created_at = record.get("created_at")
    if not isinstance(created_at, str) or not created_at:
        raise ValueError("run created_at must be an ISO-8601 string")
    try:
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("run created_at must be an ISO-8601 string") from exc

    exit_code = record.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise ValueError("run exit_code must be an integer")
    succeeded = record.get("error") is None
    result = record.get("result")
    if succeeded:
        if exit_code != 0:
            raise ValueError("successful run must have exit_code 0")
        validate_result_contract(
            result,
            expected_method_id=method_id,
            expected_params=record.get("params") or {},
        )
    elif result is not None:
        raise ValueError("failed run must not contain an evidence result")
    workspace_revision = record.get("workspace_revision")
    if workspace_revision is not None and (
        isinstance(workspace_revision, bool)
        or not isinstance(workspace_revision, int)
        or workspace_revision < 0
    ):
        raise ValueError("workspace_revision must be a non-negative integer")
    return record


def _method_contract(tx: Any, method_id: str) -> frozenset[str]:
    rows = list(
        tx.run(
            """
            MATCH (m:Method:Verigraph {
                id: $method_id, verigraph_namespace: $graph_namespace
            })-[:DESCRIBED_IN]->(p:Paper:Verigraph {
                verigraph_namespace: $graph_namespace
            })
            OPTIONAL MATCH (c:Claim:Verigraph {
                verigraph_namespace: $graph_namespace
            })-[:FROM]->(p)
            RETURN m.id AS method_id, collect(DISTINCT c.id) AS claim_ids
            """,
            method_id=method_id,
            graph_namespace=GRAPH_NAMESPACE,
        )
    )
    if len(rows) != 1 or _record_value(rows[0], "method_id") != method_id:
        raise ValueError(f"method '{method_id}' is missing or is not attached to a paper")
    return frozenset(
        claim_id
        for claim_id in (_record_value(rows[0], "claim_ids", []) or [])
        if isinstance(claim_id, str) and claim_id
    )


def _record_fingerprint(record: dict[str, Any]) -> str:
    fingerprint_fields = (
        "run_id",
        "method_id",
        "implementation_source",
        "implementation_fingerprint",
        "context_digest",
        "provisional",
        "workspace_revision",
        "backend",
        "exit_code",
        "duration_s",
        "stdout",
        "stderr",
        "created_at",
        "result",
        "error",
        "params",
        "parameter_overrides",
        "sandbox_id",
        "fallback_reason",
        "cleanup_error",
    )
    canonical = {field: record.get(field) for field in fingerprint_fields}
    payload = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _claim_run_id(
    tx: Any,
    run_id: str,
    method_id: str,
    record_hash: str,
) -> None:
    """Atomically reserve a run id for exactly one record payload."""
    rows = list(
        tx.run(
            """
            MERGE (r:Run:Verigraph {
                id: $run_id, verigraph_namespace: $graph_namespace
            })
            ON CREATE SET r.record_hash = $record_hash
            WITH r
            OPTIONAL MATCH (r)-[:IMPLEMENTS]->(m:Method:Verigraph {
                verigraph_namespace: $graph_namespace
            })
            RETURN r.record_hash AS record_hash,
                   collect(DISTINCT m.id) AS method_ids
            """,
            run_id=run_id,
            record_hash=record_hash,
            graph_namespace=GRAPH_NAMESPACE,
        )
    )
    if len(rows) != 1:
        raise ValueError(f"could not reserve run_id '{run_id}'")
    existing_hash = _record_value(rows[0], "record_hash")
    if existing_hash != record_hash:
        raise ValueError(f"run_id '{run_id}' already belongs to a different record")
    existing = {
        item
        for item in (_record_value(rows[0], "method_ids", []) or [])
        if isinstance(item, str)
    }
    if existing and existing != {method_id}:
        raise ValueError(
            f"run_id '{run_id}' is already attached to a different method"
        )


def curate_run(tx: Any, record: dict[str, Any]) -> None:
    """Curate one record; any invalid method/claim aborts the transaction."""
    record = validate_run_record(record)
    method_id = record["method_id"]
    allowed_claim_ids = _method_contract(tx, method_id)

    result = record.get("result")
    if result is not None:
        validate_result_contract(
            result,
            expected_method_id=method_id,
            expected_params=record.get("params") or {},
            allowed_claim_ids=allowed_claim_ids,
        )
    record_hash = _record_fingerprint(record)
    _claim_run_id(tx, record["run_id"], method_id, record_hash)
    status = "success" if record.get("error") is None else "failure"
    run_rows = list(
        tx.run(
            """
            MATCH (m:Method:Verigraph {
                id: $method_id, verigraph_namespace: $graph_namespace
            })
            MERGE (r:Run:Verigraph {
                id: $run_id, verigraph_namespace: $graph_namespace
            })
            SET r.method_id = $method_id,
                r.record_hash = $record_hash,
                r.implementation_source = $implementation_source,
                r.implementation_fingerprint = $implementation_fingerprint,
                r.context_digest = $context_digest,
                r.provisional = $provisional,
                r.workspace_revision = $workspace_revision,
                r.backend = $backend, r.exit_code = $exit_code,
                r.duration_s = $duration_s, r.created_at = $created_at,
                r.status = $status, r.error = $error,
                r.metrics = $metrics, r.params = $params
            MERGE (r)-[:IMPLEMENTS]->(m)
            RETURN r.id AS run_id
            """,
            run_id=record["run_id"],
            record_hash=record_hash,
            method_id=method_id,
            implementation_source=record["implementation_source"],
            implementation_fingerprint=record.get("implementation_fingerprint"),
            context_digest=record.get("context_digest"),
            provisional=record["provisional"],
            workspace_revision=record.get("workspace_revision"),
            backend=record["backend"],
            exit_code=record["exit_code"],
            duration_s=record.get("duration_s"),
            created_at=record["created_at"],
            status=status,
            error=record.get("error"),
            metrics=json.dumps((result or {}).get("metrics", {}), allow_nan=False),
            params=json.dumps(
                (result or {}).get("params", record.get("params", {})),
                allow_nan=False,
            ),
            graph_namespace=GRAPH_NAMESPACE,
        )
    )
    if len(run_rows) != 1 or _record_value(run_rows[0], "run_id") != record["run_id"]:
        raise ValueError(f"method '{method_id}' disappeared during curation")
    tx.run(
        """
        MATCH (r:Run:Verigraph {
            id: $run_id, verigraph_namespace: $graph_namespace
        })
        OPTIONAL MATCH (r)-[old:VALIDATES|REFUTES]->(:Claim:Verigraph {
            verigraph_namespace: $graph_namespace
        })
        DELETE old
        """,
        run_id=record["run_id"],
        graph_namespace=GRAPH_NAMESPACE,
    )
    tx.run(
        """
        MATCH (r:Run:Verigraph {
            id: $run_id, verigraph_namespace: $graph_namespace
        })
        MERGE (a:Artifact:Verigraph {
            id: $artifact_id, verigraph_namespace: $graph_namespace
        })
        SET a.kind = 'stdout', a.content = $content
        MERGE (r)-[:PRODUCED]->(a)
        """,
        run_id=record["run_id"],
        artifact_id=f"{record['run_id']}-stdout",
        content=(record.get("stdout") or record.get("stderr") or "")[:MAX_LOG_CHARS],
        graph_namespace=GRAPH_NAMESPACE,
    )
    for check in (result or {}).get("claim_checks", []):
        # The relationship type is safe to interpolate after strict enum validation.
        link_rows = list(
            tx.run(
                f"""
                MATCH (r:Run:Verigraph {{
                    id: $run_id, verigraph_namespace: $graph_namespace
                }}), (c:Claim:Verigraph {{
                    id: $claim_id, verigraph_namespace: $graph_namespace
                }})
                MERGE (r)-[v:{check['verdict']}]->(c)
                SET v.detail = $detail,
                    v.implementation_source = $implementation_source,
                    v.provisional = $provisional
                RETURN count(v) AS links
                """,
                run_id=record["run_id"],
                claim_id=check["claim_id"],
                detail=check["detail"],
                implementation_source=record["implementation_source"],
                provisional=record["provisional"],
                graph_namespace=GRAPH_NAMESPACE,
            )
        )
        if len(link_rows) != 1 or _record_value(link_rows[0], "links") != 1:
            raise ValueError(
                f"claim '{check['claim_id']}' disappeared during curation"
            )


def curate(record: dict[str, Any]) -> None:
    record = validate_run_record(record)
    from app.workspace import active_method_guard, get_workspace_store

    try:
        with active_method_guard(record["method_id"]):
            expected_revision = record.get("workspace_revision")
            current_revision = get_workspace_store().manifest()["revision"]
            if expected_revision is not None and expected_revision != current_revision:
                raise ValueError(
                    "run belongs to workspace revision "
                    f"{expected_revision}, current revision is {current_revision}"
                )
            with get_driver() as driver, driver.session(database=DATABASE) as session:
                session.execute_write(curate_run, record)
    except FileNotFoundError as exc:
        raise ValueError(str(exc)) from exc
    try:
        from app.cognee_memory import remember_run_sync

        remember_run_sync(record)
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_json", help="path to runs/<run_id>.json (or a run_id)")
    args = parser.parse_args()

    path = args.run_json
    if not os.path.exists(path):
        path = os.path.join(RUNS_DIR, f"{args.run_json}.json")
    with open(path, encoding="utf-8") as handle:
        record = json.load(handle)
    curate(record)
    checks = (record.get("result") or {}).get("claim_checks", [])
    status = "success" if record.get("error") is None else "failure"
    print(f"curated {record['run_id']} -> graph ({len(checks)} claim links, status={status})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
