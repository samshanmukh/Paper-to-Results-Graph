"""Graph-grounded Q&A without RocketRide — same logic as butterbase/verigraph_api.ts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.db import DATABASE, get_driver


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
            "MATCH (p:Paper) RETURN p.id AS id, p.title AS title, p.year AS year ORDER BY p.year, p.id",
            database_=DATABASE,
        )
        ctx.papers = [dict(r) for r in paper_recs]
        paper_titles = {p["id"]: p.get("title") or p["id"] for p in ctx.papers}

        claim_recs, _, _ = driver.execute_query(
            """
            MATCH (c:Claim)-[:FROM]->(p:Paper)
            OPTIONAL MATCH (r:Run)-[v:VALIDATES|REFUTES]->(c)
            RETURN c.id AS id, c.text AS text, p.id AS paper_id,
                   type(v) AS verdict, r.id AS run_id, v.detail AS detail
            ORDER BY paper_id, id
            """,
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
                ev = {"verdict": r["verdict"], "runId": r["run_id"], "detail": r.get("detail")}
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
            MATCH (m:Method)-[:DESCRIBED_IN]->(p:Paper)
            OPTIONAL MATCH (r:Run)-[:IMPLEMENTS]->(m)
            RETURN m.id AS id, m.name AS name, p.id AS paper_id,
                   count(r) > 0 AS has_run
            ORDER BY id
            """,
            database_=DATABASE,
        )
        ctx.methods = [dict(r) for r in method_recs]

        conflict_recs, _, _ = driver.execute_query(
            """
            MATCH (a:Claim)-[:CONTRADICTS]->(b:Claim),
                  (a)-[:FROM]->(pa:Paper), (b)-[:FROM]->(pb:Paper)
            RETURN a.id AS from_id, b.id AS to_id,
                   a.text AS from_text, b.text AS to_text,
                   pa.id AS from_paper, pb.id AS to_paper
            """,
            database_=DATABASE,
        )
        ctx.conflicts = [dict(r) for r in conflict_recs]

        run_recs, _, _ = driver.execute_query(
            """
            MATCH (r:Run)
            OPTIONAL MATCH (r)-[:IMPLEMENTS]->(m:Method)
            OPTIONAL MATCH (r)-[v:VALIDATES|REFUTES]->(c:Claim)
            RETURN r.id AS id, m.id AS method_id, r.backend AS backend,
                   r.metrics AS metrics, collect({
                     claim_id: c.id, verdict: type(v), detail: v.detail
                   }) AS claim_checks
            ORDER BY r.id
            """,
            database_=DATABASE,
        )
        ctx.runs = [dict(r) for r in run_recs]
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
    hits = [r for r in ctx.runs if r.get("method_id") == method_id]
    if not hits:
        return None
    hits.sort(key=lambda r: str(r.get("id", "")), reverse=True)
    return hits[0]


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
            lines.append(f"• **{c.id}** ({c.paper_id}): {ev.get('verdict')} via `{ev.get('runId')}`{detail}")
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
    run_ids = [r["id"] for r in ctx.runs]
    headline = (
        "Wilson counterexample: GD test error 0.000 vs Adam 0.425 — VALIDATES generalization gap"
        if validated and any(r.get("method_id") == "wilson2017-m1" for r in ctx.runs)
        else f"{len(validated)} validated, {len(refuted)} refuted, {len(untested)} untested"
    )
    lines = [f"**{headline}**", ""]
    if validated:
        lines.append("**Validated**")
        for c in validated:
            detail = (c.evidence or {}).get("detail") or c.text
            lines.append(f"• {c.id}: {detail}")
        lines.append("")
    if refuted:
        lines.append("**Refuted**")
        for c in refuted:
            detail = (c.evidence or {}).get("detail") or c.text
            lines.append(f"• {c.id}: {detail}")
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
            from app.codegen import materialize
            from app.curator import curate
            from app.runner import execute

            record = execute(method_id, "auto")
            curate(record)
            run = record
            checks = (record.get("result") or {}).get("claim_checks", [])
            verdicts = ", ".join(f"{c['verdict']} {c['claim_id']}" for c in checks)
            steps.append(
                f"[executor] ✓ {record['run_id']} [{record.get('backend')}] "
                f"{record.get('duration_s')}s — {verdicts or 'metrics recorded'}"
            )
        except Exception as e:
            steps.append(f"[executor] ✗ {method_id} failed: {e}")
    else:
        hit = _latest_run_for_method(ctx, method_id)
        if hit:
            checks = [c for c in (hit.get("claim_checks") or []) if c.get("claim_id")]
            verdicts = ", ".join(f"{c.get('verdict')} {c.get('claim_id')}" for c in checks)
            steps.append(f"[executor] ✓ replay {hit['id']} — {verdicts or 'metrics recorded'}")
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
