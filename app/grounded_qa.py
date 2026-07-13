"""Graph-grounded Q&A without RocketRide — same logic as butterbase/verigraph_api.ts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.db import DATABASE, GRAPH_NAMESPACE, get_driver


@dataclass
class ClaimRow:
    id: str
    text: str
    paper_id: str
    paper_title: str
    evidence: dict[str, Any] | None = None


@dataclass
class WorkspaceCtx:
    papers: list[dict[str, Any]] = field(default_factory=list)
    claims: list[ClaimRow] = field(default_factory=list)
    methods: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    runs: list[dict[str, Any]] = field(default_factory=list)
    claim_by_id: dict[str, ClaimRow] = field(default_factory=dict)


def _load_workspace() -> WorkspaceCtx:
    ctx = WorkspaceCtx()
    with get_driver() as driver:
        paper_recs, _, _ = driver.execute_query(
            "MATCH (p:Paper:Verigraph {verigraph_namespace: $graph_namespace}) "
            "RETURN p.id AS id, p.title AS title, p.year AS year ORDER BY p.year, p.id",
            graph_namespace=GRAPH_NAMESPACE,
            database_=DATABASE,
        )
        ctx.papers = [dict(r) for r in paper_recs]
        paper_titles = {p["id"]: p.get("title") or p["id"] for p in ctx.papers}

        claim_recs, _, _ = driver.execute_query(
            """
            MATCH (c:Claim:Verigraph {verigraph_namespace: $graph_namespace})
                  -[:FROM]->
                  (p:Paper:Verigraph {verigraph_namespace: $graph_namespace})
            OPTIONAL MATCH (r:Run:Verigraph {verigraph_namespace: $graph_namespace})
                           -[v:VALIDATES|REFUTES]->(c)
            WHERE r.status = 'success'
               OR (r.status IS NULL AND r.error IS NULL AND r.exit_code = 0)
            RETURN c.id AS id, c.text AS text, p.id AS paper_id,
                   type(v) AS verdict, r.id AS run_id, v.detail AS detail,
                   r.implementation_source AS implementation_source,
                   r.implementation_fingerprint AS implementation_fingerprint,
                   r.context_digest AS context_digest,
                   coalesce(v.provisional, r.provisional, false) AS marked_provisional,
                   r.created_at AS run_created_at
            ORDER BY paper_id, id, run_created_at DESC, run_id DESC
            """,
            graph_namespace=GRAPH_NAMESPACE,
            database_=DATABASE,
        )
        seen: set[str] = set()
        for r in claim_recs:
            cid = r["id"]
            if cid in seen:
                continue
            seen.add(cid)
            ev = None
            if r.get("verdict") and r.get("run_id"):
                source = r.get("implementation_source") or "unknown"
                ev = {
                    "verdict": r["verdict"],
                    "runId": r["run_id"],
                    "detail": r.get("detail"),
                    "implementationSource": source,
                    "provisional": (
                        bool(r.get("marked_provisional"))
                        or source != "curated"
                        or not r.get("implementation_fingerprint")
                        or not r.get("context_digest")
                    ),
                }
            row = ClaimRow(
                id=cid,
                text=r.get("text") or "",
                paper_id=r["paper_id"],
                paper_title=paper_titles.get(r["paper_id"], r["paper_id"]),
                evidence=ev,
            )
            ctx.claims.append(row)
            ctx.claim_by_id[cid] = row

        method_recs, _, _ = driver.execute_query(
            """
            MATCH (m:Method:Verigraph {verigraph_namespace: $graph_namespace})
                  -[:DESCRIBED_IN]->
                  (p:Paper:Verigraph {verigraph_namespace: $graph_namespace})
            OPTIONAL MATCH (r:Run:Verigraph {verigraph_namespace: $graph_namespace})
                           -[:IMPLEMENTS]->(m)
            WHERE r.status = 'success'
               OR (r.status IS NULL AND r.error IS NULL AND r.exit_code = 0)
            RETURN m.id AS id, m.name AS name, p.id AS paper_id,
                   count(r) > 0 AS has_run
            ORDER BY id
            """,
            graph_namespace=GRAPH_NAMESPACE,
            database_=DATABASE,
        )
        ctx.methods = [dict(r) for r in method_recs]

        conflict_recs, _, _ = driver.execute_query(
            """
            MATCH (a:Claim:Verigraph {verigraph_namespace: $graph_namespace})
                  -[:CONTRADICTS]->
                  (b:Claim:Verigraph {verigraph_namespace: $graph_namespace}),
                  (a)-[:FROM]->
                  (pa:Paper:Verigraph {verigraph_namespace: $graph_namespace}),
                  (b)-[:FROM]->
                  (pb:Paper:Verigraph {verigraph_namespace: $graph_namespace})
            RETURN a.id AS from_id, b.id AS to_id,
                   a.text AS from_text, b.text AS to_text,
                   pa.id AS from_paper, pb.id AS to_paper
            """,
            graph_namespace=GRAPH_NAMESPACE,
            database_=DATABASE,
        )
        ctx.conflicts = [dict(r) for r in conflict_recs]

        run_recs, _, _ = driver.execute_query(
            """
            MATCH (r:Run:Verigraph {verigraph_namespace: $graph_namespace})
            OPTIONAL MATCH (r)-[:IMPLEMENTS]->
                           (m:Method:Verigraph {verigraph_namespace: $graph_namespace})
            OPTIONAL MATCH (r)-[v:VALIDATES|REFUTES]->
                           (c:Claim:Verigraph {verigraph_namespace: $graph_namespace})
            RETURN r.id AS id, m.id AS method_id, r.backend AS backend,
                   r.metrics AS metrics, r.status AS status,
                   r.implementation_source AS implementation_source,
                   r.implementation_fingerprint AS implementation_fingerprint,
                   r.context_digest AS context_digest,
                   r.provisional AS provisional,
                   r.error AS error, r.exit_code AS exit_code,
                   r.created_at AS created_at, collect({
                     claim_id: c.id, verdict: type(v), detail: v.detail
                   }) AS claim_checks
            ORDER BY created_at DESC, id DESC
            """,
            graph_namespace=GRAPH_NAMESPACE,
            database_=DATABASE,
        )
        ctx.runs = [dict(r) for r in run_recs]
        for run in ctx.runs:
            run["provisional"] = (
                bool(run.get("provisional"))
                or run.get("implementation_source") != "curated"
                or not run.get("implementation_fingerprint")
                or not run.get("context_digest")
            )
    return ctx


def _claims_with_evidence(ctx: WorkspaceCtx) -> list[ClaimRow]:
    return [c for c in ctx.claims if c.evidence]


def _untested_claims(ctx: WorkspaceCtx) -> list[ClaimRow]:
    return [c for c in ctx.claims if not c.evidence]


def _next_method_to_run(ctx: WorkspaceCtx) -> str | None:
    untested = [m for m in ctx.methods if not m.get("has_run")]
    for m in untested:
        if m["id"] == "wilson2017-m1":
            return m["id"]
    if untested:
        return untested[0]["id"]
    return ctx.methods[0]["id"] if ctx.methods else None


def _latest_run_for_method(ctx: WorkspaceCtx, method_id: str) -> dict[str, Any] | None:
    hits = [
        run
        for run in ctx.runs
        if run.get("method_id") == method_id and _run_is_successful(run)
    ]
    if not hits:
        return None
    return max(hits, key=_run_sort_key)


def _run_is_successful(run: dict[str, Any]) -> bool:
    status = run.get("status")
    if status is not None:
        return status == "success"
    exit_code = run.get("exit_code")
    return run.get("error") is None and exit_code in (None, 0)


def _run_sort_key(run: dict[str, Any]) -> tuple[str, str]:
    return str(run.get("created_at") or ""), str(run.get("id") or "")


def _provisional_label(source: Any, provisional: Any = None) -> str:
    if source == "curated" and provisional is False:
        return ""
    if source == "llm":
        return " [provisional LLM-generated]"
    return " [provisional unverified provenance]"


def _provisional_prefix(source: Any, provisional: Any = None) -> str:
    if source == "curated" and provisional is False:
        return ""
    if source == "llm":
        return "Provisional LLM-generated "
    return "Provisional unverified-provenance "


def _decode_metrics(run: dict[str, Any]) -> dict[str, Any]:
    metrics = run.get("metrics")
    if isinstance(metrics, dict):
        return metrics
    if isinstance(metrics, str):
        try:
            parsed = json.loads(metrics)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _format_metric(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return f"{value:.3f}"
    return str(value)


def _brief_headline(
    ctx: WorkspaceCtx,
    validated: list[ClaimRow],
    refuted: list[ClaimRow],
    untested: list[ClaimRow],
) -> str:
    covered_ids = {
        claim.evidence.get("runId")
        for claim in validated + refuted
        if claim.evidence and claim.evidence.get("runId")
    }
    covered_runs = [
        run
        for run in ctx.runs
        if run.get("id") in covered_ids and _run_is_successful(run)
    ]
    if covered_runs:
        latest = max(covered_runs, key=_run_sort_key)
        metrics = _decode_metrics(latest)
        gd_error = metrics.get("test_error_gd", metrics.get("test_error_sgd"))
        adam_error = metrics.get("test_error_adam")
        if gd_error is not None and adam_error is not None:
            prefix = (
                "Wilson counterexample"
                if latest.get("method_id") == "wilson2017-m1"
                else "Latest optimizer evidence"
            )
            provisional_prefix = _provisional_prefix(
                latest.get("implementation_source"), latest.get("provisional")
            )
            if provisional_prefix:
                prefix = f"{provisional_prefix}{prefix.lower()}"
            return (
                f"{prefix}: GD test error {_format_metric(gd_error)} vs "
                f"Adam {_format_metric(adam_error)}"
            )
        scalar_metrics = [
            (key, value)
            for key, value in sorted(metrics.items())
            if isinstance(value, (str, int, float, bool))
        ]
        if scalar_metrics:
            summary = ", ".join(
                f"{key}={_format_metric(value)}" for key, value in scalar_metrics[:2]
            )
            provenance = _provisional_prefix(
                latest.get("implementation_source"), latest.get("provisional")
            )
            return f"{provenance}latest evidence run {latest.get('id')}: {summary}"
    return f"{len(validated)} validated, {len(refuted)} refuted, {len(untested)} untested"


def answer_ask(question: str, ctx: WorkspaceCtx | None = None) -> str:
    ctx = ctx or _load_workspace()
    q = (question or "").lower().strip()
    evidenced = _claims_with_evidence(ctx)
    untested = _untested_claims(ctx)

    if re.search(r"executable evidence|which claims|validated|refuted|have evidence|runs? (show|prove)", q):
        if not evidenced:
            return (
                "No claims have executable evidence yet. "
                f"There are {len(untested)} untested claims across {len(ctx.papers)} papers. "
                "Select **wilson2017-m1** on the graph and press **RUN** to execute the Wilson counterexample."
            )
        lines = []
        for c in evidenced:
            ev = c.evidence or {}
            detail = f" — {ev['detail']}" if ev.get("detail") else ""
            provisional = _provisional_label(
                ev.get("implementationSource"), ev.get("provisional")
            )
            lines.append(
                f"• **{c.id}** ({c.paper_id}): {ev.get('verdict')}{provisional} "
                f"via `{ev.get('runId')}`{detail}"
            )
        pending = ""
        if untested:
            ids = ", ".join(c.id for c in untested[:4])
            extra = f" (+{len(untested) - 4} more)" if len(untested) > 4 else ""
            pending = f"\n\nStill untested: {ids}{extra}"
        return (
            f"{len(evidenced)} claim(s) have executable evidence:\n\n"
            + "\n".join(lines)
            + pending
            + "\n\n_(Graph-grounded from Neo4j — enable RocketRide for live agent reasoning.)_"
        )

    if re.search(r"agree|consensus|same (view|conclusion)|papers.*adam", q):
        if not ctx.conflicts:
            return "The loaded papers do not declare any CONTRADICTS edges."
        adam_conflicts = [
            c
            for c in ctx.conflicts
            if re.search(r"adam", c.get("from_paper", "") + c.get("to_paper", "") + c.get("from_text", "") + c.get("to_text", ""), re.I)
        ]
        sample = (adam_conflicts or ctx.conflicts)[:3]
        lines = [
            f"• **{c['from_id']}** ({c['from_paper']}) CONTRADICTS **{c['to_id']}** ({c['to_paper']})"
            for c in sample
        ]
        extra = f"\n\n…plus {len(ctx.conflicts) - len(sample)} more." if len(ctx.conflicts) > len(sample) else ""
        return (
            "They do **not** fully agree — cross-paper conflicts:\n\n"
            + "\n".join(lines)
            + extra
            + "\n\nWilson's counterexample (**wilson2017-m1**) adjudicates Adam generalization on the constructed problem."
        )

    paper_match = re.search(r"contradict[s]?\s+(\w[\w-]*)", q) or re.search(r"(\w[\w-]*)\s+contradict", q)
    target_paper = paper_match.group(1) if paper_match else None
    if re.search(r"contradict|conflict|oppose|disagree", q):
        hits = (
            [c for c in ctx.conflicts if c.get("from_paper") == target_paper or c.get("to_paper") == target_paper]
            if target_paper
            else ctx.conflicts
        )
        if not hits:
            return (
                f"No CONTRADICTS edges involve **{target_paper}**."
                if target_paper
                else "No CONTRADICTS edges in the graph."
            )
        lines = [
            f"• **{c['from_id']}** ({c['from_paper']}) → **{c['to_id']}** ({c['to_paper']})\n"
            f'  "{c.get("from_text", "")}"\n  vs "{c.get("to_text", "")}"'
            for c in hits
        ]
        suffix = f" involving **{target_paper}**" if target_paper else ""
        return f"Found {len(hits)} contradiction(s){suffix}:\n\n" + "\n\n".join(lines)

    if re.search(r"cognee|semantic memory|memory recall", q):
        try:
            from app.cognee_memory import is_enabled, recall_context_sync

            if is_enabled():
                hits = recall_context_sync(question, top_k=3)
                if hits:
                    return (
                        "**Cognee recall** (semantic memory over papers + runs):\n\n"
                        + hits
                        + "\n\n_(Also query the graph for structured VALIDATES/REFUTES edges.)_"
                    )
                return "Cognee is enabled but returned no hits for that query — try syncing with `scripts/sync_cognee.py`."
            return (
                "Cognee semantic memory is disabled. Set `COGNEE_ENABLED=true` in `.env` "
                "(local mode: `ROCKETRIDE_GATEWAY_*`; cloud: `COGNEE_CLOUD` + API key)."
            )
        except Exception as e:
            return f"Cognee check failed: {e}"

    if re.search(r"recommend|what (should|to) run|next (method|experiment)|untested", q):
        method_id = _next_method_to_run(ctx)
        untested_ids = [c.id for c in untested]
        return (
            f"Graph audit: {len(ctx.conflicts)} conflict(s), {len(untested)} untested claim(s).\n\n"
            + (f"Recommended next run: **{method_id}**" if method_id else "All methods have runs.")
            + (f"\n\nUntested: {', '.join(untested_ids[:5])}" if untested_ids else "")
        )

    if re.search(r"brief|summary|overview|status", q):
        return answer_brief(ctx)["answer"]

    validated = sum(1 for c in evidenced if c.evidence and c.evidence.get("verdict") == "VALIDATES")
    refuted = sum(1 for c in evidenced if c.evidence and c.evidence.get("verdict") == "REFUTES")
    return (
        f"Workspace: **{len(ctx.papers)}** papers, **{len(ctx.claims)}** claims, **{len(ctx.runs)}** run(s).\n\n"
        f"Evidence: {validated} validated, {refuted} refuted, {len(untested)} untested. "
        f"{len(ctx.conflicts)} CONTRADICTS edge(s).\n\n"
        "Try: _Which claims have executable evidence?_ · _What contradicts adam2014?_ · _cognee check_"
    )


def answer_investigate(ctx: WorkspaceCtx | None = None) -> dict[str, Any]:
    ctx = ctx or _load_workspace()
    untested = [c.id for c in _untested_claims(ctx)]
    method_id = _next_method_to_run(ctx)
    conflict_lines = [
        f"• {c['from_id']} ({c['from_paper']}) CONTRADICTS {c['to_id']} ({c['to_paper']})"
        for c in ctx.conflicts[:4]
    ]
    prose = (
        "Investigator audit (Neo4j graph):\n\n"
        f"**{len(ctx.conflicts)}** cross-paper CONTRADICTS conflict(s):\n"
        + ("\n".join(conflict_lines) if conflict_lines else "• none")
        + f"\n\n**{len(untested)}** untested claim(s)"
        + (f": {', '.join(untested[:6])}" if untested else "")
        + (f"\n\nRecommend running **{method_id}** next." if method_id else "")
    )
    return {
        "answer": prose,
        "agent": "investigator",
        "header_present": True,
        "grounded": True,
        "payload": {
            "conflicts": [
                {"from": c["from_id"], "to": c["to_id"], "type": "CONTRADICTS"} for c in ctx.conflicts
            ],
            "untested_claims": untested,
            "recommended_method_id": method_id,
        },
    }


def answer_brief(ctx: WorkspaceCtx | None = None) -> dict[str, Any]:
    ctx = ctx or _load_workspace()
    evidenced = _claims_with_evidence(ctx)
    validated = [c for c in evidenced if c.evidence and c.evidence.get("verdict") == "VALIDATES"]
    refuted = [c for c in evidenced if c.evidence and c.evidence.get("verdict") == "REFUTES"]
    untested = _untested_claims(ctx)
    evidence_run_ids = {
        claim.evidence.get("runId")
        for claim in evidenced
        if claim.evidence and claim.evidence.get("runId")
    }
    covered_runs = [
        run
        for run in ctx.runs
        if run.get("id") in evidence_run_ids and _run_is_successful(run)
    ]
    covered_runs.sort(key=_run_sort_key, reverse=True)
    known_ids = {run.get("id") for run in covered_runs}
    run_ids = [run["id"] for run in covered_runs]
    run_ids.extend(sorted(evidence_run_ids - known_ids, reverse=True))
    headline = _brief_headline(ctx, validated, refuted, untested)
    lines = [f"**{headline}**", ""]
    if validated:
        lines.append("**Validated**")
        for c in validated:
            detail = (c.evidence or {}).get("detail") or c.text
            provisional = _provisional_label(
                (c.evidence or {}).get("implementationSource"),
                (c.evidence or {}).get("provisional"),
            )
            lines.append(f"• {c.id}{provisional}: {detail}")
        lines.append("")
    if refuted:
        lines.append("**Refuted**")
        for c in refuted:
            detail = (c.evidence or {}).get("detail") or c.text
            provisional = _provisional_label(
                (c.evidence or {}).get("implementationSource"),
                (c.evidence or {}).get("provisional"),
            )
            lines.append(f"• {c.id}{provisional}: {detail}")
        lines.append("")
    if untested:
        lines.append(f"**Untested** ({len(untested)}): {', '.join(c.id for c in untested[:5])}")
    return {
        "answer": "\n".join(lines) + "\n\n_(Evidence brief from Neo4j runs.)_",
        "agent": "reporter",
        "header_present": True,
        "grounded": True,
        "payload": {
            "run_ids_covered": run_ids,
            "headline": headline,
            "validated": [c.id for c in validated],
            "refuted": [c.id for c in refuted],
            "provisional": [
                c.id
                for c in evidenced
                if (c.evidence or {}).get("provisional") is not False
                or (c.evidence or {}).get("implementationSource") != "curated"
            ],
            "untested": [c.id for c in untested],
        },
    }


def answer_conduct(*, skip_execute: bool = False) -> dict[str, Any]:
    ctx = _load_workspace()
    inv = answer_investigate(ctx)
    method_id = (inv.get("payload") or {}).get("recommended_method_id") or "wilson2017-m1"
    steps = [
        f"[investigator] ✓ {len(inv['payload']['conflicts'])} conflicts, "
        f"{len(inv['payload']['untested_claims'])} untested — recommended {method_id}"
    ]
    run = None
    record = None
    if not skip_execute and method_id:
        try:
            from app.curator import curate
            from app.runner import execute
            from app.workspace import active_method_guard

            with active_method_guard(method_id):
                record = execute(method_id, "auto")
                curate(record)
            run = record
            if record.get("error"):
                steps.append(
                    f"[executor] ✗ {record['run_id']} failed: {record['error']}"
                )
            else:
                checks = (record.get("result") or {}).get("claim_checks", [])
                verdicts = ", ".join(
                    f"{check['verdict']} {check['claim_id']}" for check in checks
                )
                steps.append(
                    f"[executor] ✓ {record['run_id']} [{record.get('backend')}] "
                    f"{record.get('duration_s')}s"
                    + _provisional_label(
                        record.get("implementation_source"), record.get("provisional")
                    )
                    + f" — {verdicts or 'metrics recorded'}"
                )
        except Exception as e:
            steps.append(f"[executor] ✗ {method_id} failed: {e}")
    else:
        hit = _latest_run_for_method(ctx, method_id)
        if hit:
            checks = [c for c in (hit.get("claim_checks") or []) if c.get("claim_id")]
            verdicts = ", ".join(f"{c.get('verdict')} {c.get('claim_id')}" for c in checks)
            provisional = _provisional_label(
                hit.get("implementation_source"), hit.get("provisional")
            )
            steps.append(
                f"[executor] ✓ replay {hit['id']}{provisional} — "
                f"{verdicts or 'metrics recorded'}"
            )
            run = hit
        else:
            steps.append(f"[executor] ✗ no run for {method_id}")

    ctx = _load_workspace()
    brief = answer_brief(ctx)
    steps.append(
        f"[reporter] ✓ brief over {len(brief['payload']['run_ids_covered'])} runs — "
        f"{brief['payload']['headline'][:90]}"
    )
    return {
        "answer": "\n".join(steps) + "\n\n" + brief["answer"],
        "steps": steps,
        "method_id": method_id,
        "run_id": (record or run or {}).get("run_id") or (run or {}).get("id"),
        "investigator": inv["payload"],
        "reporter": brief["payload"],
        "grounded": True,
    }


def agent_unavailable(answer: str) -> bool:
    return answer.strip().startswith("(agent unavailable:")
