"""Workspace insights: coverage stats, conflict adjudication, run history, export.

Used by /api/insights, /api/conflicts, /api/runs, and /api/export.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from app.db import DATABASE, OUR_LABELS, get_driver

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(ROOT, "runs")


def _claim_evidence(driver) -> list[dict]:
    records, _, _ = driver.execute_query(
        """
        MATCH (c:Claim)-[:FROM]->(p:Paper)
        OPTIONAL MATCH (r:Run)-[v:VALIDATES|REFUTES]->(c)
        RETURN p.id AS paper, p.title AS paper_title,
               c.id AS claim, c.text AS text,
               CASE WHEN r IS NULL THEN null ELSE type(v) END AS verdict,
               r.id AS run_id,
               CASE WHEN r IS NULL THEN null ELSE properties(v) END AS verdict_props
        ORDER BY paper, claim
        """,
        database_=DATABASE,
    )
    rows = []
    for r in records:
        d = dict(r)
        props = d.pop("verdict_props", None) or {}
        d["detail"] = props.get("detail") if isinstance(props, dict) else None
        rows.append(d)
    return rows


def _conflicts_raw(driver) -> list[dict]:
    records, _, _ = driver.execute_query(
        """
        MATCH (a:Claim)-[:CONTRADICTS]->(b:Claim),
              (a)-[:FROM]->(pa:Paper), (b)-[:FROM]->(pb:Paper)
        OPTIONAL MATCH (ra:Run)-[va:VALIDATES|REFUTES]->(a)
        OPTIONAL MATCH (rb:Run)-[vb:VALIDATES|REFUTES]->(b)
        RETURN a.id AS from_claim, a.text AS from_text,
               pa.id AS from_paper, pa.title AS from_paper_title,
               CASE WHEN ra IS NULL THEN null ELSE type(va) END AS from_verdict,
               ra.id AS from_run_id,
               b.id AS to_claim, b.text AS to_text,
               pb.id AS to_paper, pb.title AS to_paper_title,
               CASE WHEN rb IS NULL THEN null ELSE type(vb) END AS to_verdict,
               rb.id AS to_run_id
        ORDER BY from_paper, to_paper
        """,
        database_=DATABASE,
    )
    return [dict(r) for r in records]


def _methods(driver) -> list[dict]:
    records, _, _ = driver.execute_query(
        """
        MATCH (m:Method)-[:DESCRIBED_IN]->(p:Paper)
        OPTIONAL MATCH (r:Run)-[:IMPLEMENTS]->(m)
        WITH m, p, count(r) AS run_count, collect(r.id)[0] AS latest_run
        RETURN m.id AS method, m.name AS name, p.id AS paper,
               m.runnable_hint AS runnable_hint,
               run_count, latest_run
        ORDER BY method
        """,
        database_=DATABASE,
    )
    return [dict(r) for r in records]


def _counts(driver) -> dict[str, int]:
    out: dict[str, int] = {}
    for label in OUR_LABELS:
        records, _, _ = driver.execute_query(
            f"MATCH (n:{label}) RETURN count(n) AS c",
            database_=DATABASE,
        )
        out[label] = records[0]["c"] if records else 0
    return out


def _adjudicate(conflict: dict) -> dict:
    """Classify a CONTRADICTS pair based on executable evidence on each side."""
    fv, tv = conflict.get("from_verdict"), conflict.get("to_verdict")
    if not fv and not tv:
        status = "untested"
        summary = "Neither side has a sandbox run yet."
    elif fv == "VALIDATES" and tv == "VALIDATES":
        status = "both_supported"
        summary = "Both claims have validating runs — the conflict remains live."
    elif fv == "VALIDATES" and (tv in (None, "REFUTES") or tv == "REFUTES"):
        if tv == "REFUTES":
            status = "from_wins"
            summary = f"{conflict['from_claim']} validated; {conflict['to_claim']} refuted."
        elif tv is None:
            status = "from_leading"
            summary = f"{conflict['from_claim']} validated; opposing claim untested."
        else:
            status = "from_leading"
            summary = f"{conflict['from_claim']} validated; opposing claim untested."
    elif tv == "VALIDATES" and (fv in (None, "REFUTES") or fv == "REFUTES"):
        if fv == "REFUTES":
            status = "to_wins"
            summary = f"{conflict['to_claim']} validated; {conflict['from_claim']} refuted."
        else:
            status = "to_leading"
            summary = f"{conflict['to_claim']} validated; opposing claim untested."
    elif fv == "REFUTES" and tv == "REFUTES":
        status = "both_refuted"
        summary = "Both sides were refuted by runs."
    elif fv == "REFUTES" and tv is None:
        status = "from_refuted"
        summary = f"{conflict['from_claim']} refuted; opposing claim untested."
    elif tv == "REFUTES" and fv is None:
        status = "to_refuted"
        summary = f"{conflict['to_claim']} refuted; opposing claim untested."
    else:
        status = "mixed"
        summary = "Mixed evidence — inspect both claim cards."
    return {**conflict, "status": status, "summary": summary}


def list_conflicts() -> list[dict]:
    with get_driver() as driver:
        return [_adjudicate(c) for c in _conflicts_raw(driver)]


def list_runs(limit: int = 50) -> list[dict]:
    """Prefer on-disk run records; fall back to Neo4j Run nodes."""
    disk: list[dict] = []
    if os.path.isdir(RUNS_DIR):
        for name in sorted(os.listdir(RUNS_DIR), reverse=True):
            if not name.endswith(".json"):
                continue
            path = os.path.join(RUNS_DIR, name)
            try:
                with open(path) as f:
                    rec = json.load(f)
                disk.append({
                    "run_id": rec.get("run_id") or name[:-5],
                    "method_id": rec.get("method_id"),
                    "backend": rec.get("backend"),
                    "exit_code": rec.get("exit_code"),
                    "duration_s": rec.get("duration_s"),
                    "error": rec.get("error"),
                    "metrics": (rec.get("result") or {}).get("metrics") or {},
                    "claim_checks": (rec.get("result") or {}).get("claim_checks") or [],
                    "source": "disk",
                })
            except Exception:
                continue
            if len(disk) >= limit:
                return disk

    with get_driver() as driver:
        records, _, _ = driver.execute_query(
            """
            MATCH (r:Run)
            OPTIONAL MATCH (r)-[:IMPLEMENTS]->(m:Method)
            OPTIONAL MATCH (r)-[v:VALIDATES|REFUTES]->(c:Claim)
            WITH r, m,
                 collect({claim: c.id, verdict: type(v), detail: v.detail}) AS checks
            RETURN r.id AS run_id, m.id AS method_id,
                   r.backend AS backend, r.exit_code AS exit_code,
                   r.duration_s AS duration_s, r.error AS error,
                   r.metrics AS metrics, checks
            ORDER BY r.id DESC
            LIMIT $limit
            """,
            limit=limit,
            database_=DATABASE,
        )
        neo = []
        for r in records:
            d = dict(r)
            metrics = d.get("metrics")
            if isinstance(metrics, str):
                try:
                    metrics = json.loads(metrics)
                except Exception:
                    metrics = {}
            checks = [c for c in (d.get("checks") or []) if c.get("claim")]
            neo.append({
                "run_id": d.get("run_id"),
                "method_id": d.get("method_id"),
                "backend": d.get("backend"),
                "exit_code": d.get("exit_code"),
                "duration_s": d.get("duration_s"),
                "error": d.get("error"),
                "metrics": metrics or {},
                "claim_checks": checks,
                "source": "neo4j",
            })
        return disk or neo


def workspace_insights() -> dict[str, Any]:
    with get_driver() as driver:
        claims = _claim_evidence(driver)
        conflicts = [_adjudicate(c) for c in _conflicts_raw(driver)]
        methods = _methods(driver)
        counts = _counts(driver)

    validated = sum(1 for c in claims if c.get("verdict") == "VALIDATES")
    refuted = sum(1 for c in claims if c.get("verdict") == "REFUTES")
    untested = sum(1 for c in claims if not c.get("verdict"))
    total_claims = len(claims)
    coverage = round(100 * (validated + refuted) / total_claims, 1) if total_claims else 0.0

    conflict_stats = {
        "total": len(conflicts),
        "untested": sum(1 for c in conflicts if c["status"] == "untested"),
        "adjudicated": sum(
            1 for c in conflicts
            if c["status"] in ("from_wins", "to_wins", "both_supported", "both_refuted",
                               "from_leading", "to_leading", "from_refuted", "to_refuted", "mixed")
        ),
        "resolved": sum(1 for c in conflicts if c["status"] in ("from_wins", "to_wins")),
        "both_supported": sum(1 for c in conflicts if c["status"] == "both_supported"),
    }

    runnable = [m for m in methods if m.get("runnable_hint") or True]
    never_run = [m for m in methods if not m.get("run_count")]
    next_method = never_run[0]["method"] if never_run else (methods[0]["method"] if methods else None)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "papers": counts.get("Paper", 0),
            "claims": counts.get("Claim", 0),
            "methods": counts.get("Method", 0),
            "runs": counts.get("Run", 0),
            "artifacts": counts.get("Artifact", 0),
            "nodes": sum(counts.values()),
        },
        "evidence": {
            "total_claims": total_claims,
            "validated": validated,
            "refuted": refuted,
            "untested": untested,
            "coverage_pct": coverage,
        },
        "conflicts": conflict_stats,
        "methods": {
            "total": len(methods),
            "with_runs": sum(1 for m in methods if m.get("run_count")),
            "never_run": len(never_run),
            "next_recommended": next_method,
            "items": methods,
        },
        "conflict_rows": conflicts,
        "claim_rows": claims,
    }


def export_workspace() -> dict[str, Any]:
    """Full workspace snapshot for download / archival."""
    insights = workspace_insights()
    with get_driver() as driver:
        node_recs, _, _ = driver.execute_query(
            """
            MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels)
            RETURN elementId(n) AS eid, labels(n)[0] AS label,
                   coalesce(n.id, n.name) AS key,
                   properties(n) AS props
            """,
            labels=OUR_LABELS,
            database_=DATABASE,
        )
        edge_recs, _, _ = driver.execute_query(
            """
            MATCH (a)-[r]->(b)
            WHERE any(l IN labels(a) WHERE l IN $labels)
              AND any(l IN labels(b) WHERE l IN $labels)
            RETURN elementId(a) AS src, elementId(b) AS dst, type(r) AS rel,
                   properties(r) AS props
            """,
            labels=OUR_LABELS,
            database_=DATABASE,
        )
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "format": "verigraph-workspace-v1",
        "insights": {
            "counts": insights["counts"],
            "evidence": insights["evidence"],
            "conflicts": insights["conflicts"],
        },
        "conflicts": insights["conflict_rows"],
        "evidence": insights["claim_rows"],
        "runs": list_runs(limit=200),
        "graph": {
            "nodes": [dict(r) for r in node_recs],
            "edges": [dict(r) for r in edge_recs],
        },
    }
