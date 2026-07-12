"""Research productivity tools: compare runs, batch queue, briefs, claim timeline."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(ROOT, "runs")
IMPL_DIR = os.path.join(ROOT, "papers", "impl")


def _disk_runs(limit: int = 500) -> list[dict]:
    out: list[dict] = []
    if not os.path.isdir(RUNS_DIR):
        return out
    for name in sorted(os.listdir(RUNS_DIR), reverse=True):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(RUNS_DIR, name)) as f:
                rec = json.load(f)
            out.append(rec)
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out


def _normalize_run(rec: dict) -> dict:
    result = rec.get("result") or {}
    metrics = result.get("metrics") if isinstance(result, dict) else None
    if metrics is None:
        metrics = rec.get("metrics") or {}
    checks = result.get("claim_checks") if isinstance(result, dict) else None
    if checks is None:
        checks = rec.get("claim_checks") or []
    if isinstance(checks, dict):
        checks = checks.get("items") or []
    return {
        "run_id": rec.get("run_id") or rec.get("id"),
        "method_id": rec.get("method_id"),
        "backend": rec.get("backend"),
        "exit_code": rec.get("exit_code"),
        "duration_s": rec.get("duration_s"),
        "error": rec.get("error"),
        "params": rec.get("params") or {},
        "metrics": metrics or {},
        "claim_checks": checks or [],
        "created_at": rec.get("created_at"),
        "replay": bool(rec.get("replay")),
    }


def load_runs(limit: int = 200) -> list[dict]:
    """Prefer disk records; fall back to insights.list_runs (Neo4j/Butterbase callers wrap)."""
    disk = [_normalize_run(r) for r in _disk_runs(limit)]
    if disk:
        return disk
    try:
        from app.insights import list_runs

        return [_normalize_run(r) for r in list_runs(limit=limit)]
    except Exception:
        return []


def compare_runs(run_a: str, run_b: str, runs: list[dict] | None = None) -> dict[str, Any]:
    pool = {_normalize_run(r)["run_id"]: _normalize_run(r) for r in (runs or load_runs(500))}
    a = pool.get(run_a)
    b = pool.get(run_b)
    if not a or not b:
        missing = [x for x, y in ((run_a, a), (run_b, b)) if not y]
        raise KeyError(f"run(s) not found: {', '.join(missing)}")
    if a.get("method_id") and b.get("method_id") and a["method_id"] != b["method_id"]:
        # still allow compare, but flag
        same_method = False
    else:
        same_method = True

    params_a, params_b = a.get("params") or {}, b.get("params") or {}
    keys = sorted(set(params_a) | set(params_b))
    param_diff = []
    for k in keys:
        va, vb = params_a.get(k), params_b.get(k)
        if str(va) != str(vb):
            param_diff.append({"key": k, "a": va, "b": vb})

    metrics_a, metrics_b = a.get("metrics") or {}, b.get("metrics") or {}
    mkeys = sorted(set(metrics_a) | set(metrics_b))
    metric_diff = []
    for k in mkeys:
        va, vb = metrics_a.get(k), metrics_b.get(k)
        delta = None
        try:
            if va is not None and vb is not None:
                delta = float(vb) - float(va)
        except (TypeError, ValueError):
            delta = None
        metric_diff.append({
            "key": k, "a": va, "b": vb, "delta": delta,
            "changed": str(va) != str(vb),
        })

    checks_a = {c.get("claim_id"): c for c in (a.get("claim_checks") or []) if c.get("claim_id")}
    checks_b = {c.get("claim_id"): c for c in (b.get("claim_checks") or []) if c.get("claim_id")}
    claim_diff = []
    for cid in sorted(set(checks_a) | set(checks_b)):
        ca, cb = checks_a.get(cid) or {}, checks_b.get(cid) or {}
        claim_diff.append({
            "claim_id": cid,
            "a": ca.get("verdict"),
            "b": cb.get("verdict"),
            "a_detail": ca.get("detail"),
            "b_detail": cb.get("detail"),
            "flipped": ca.get("verdict") != cb.get("verdict"),
        })

    return {
        "same_method": same_method,
        "method_id": a.get("method_id") or b.get("method_id"),
        "a": a,
        "b": b,
        "param_diff": param_diff,
        "metric_diff": metric_diff,
        "claim_diff": claim_diff,
        "summary": {
            "params_changed": len(param_diff),
            "metrics_changed": sum(1 for m in metric_diff if m["changed"]),
            "verdicts_flipped": sum(1 for c in claim_diff if c["flipped"]),
        },
    }


def methods_never_run(runs: list[dict] | None = None, method_ids: list[str] | None = None) -> list[str]:
    if method_ids is None:
        method_ids = [
            name[:-3] for name in sorted(os.listdir(IMPL_DIR))
            if name.endswith(".py")
        ] if os.path.isdir(IMPL_DIR) else []
        try:
            from app.insights import workspace_insights
            items = (workspace_insights().get("methods") or {}).get("items") or []
            if items:
                method_ids = [m["method"] for m in items]
        except Exception:
            pass
    done = {(_normalize_run(r).get("method_id")) for r in (runs or load_runs(500))}
    return [m for m in method_ids if m and m not in done]


def claim_timeline(runs: list[dict] | None = None) -> list[dict]:
    """Chronological verdict events per claim (oldest → newest)."""
    ordered = list(reversed(load_runs(500) if runs is None else [_normalize_run(r) for r in runs]))
    # disk load_runs is newest-first; reverse for timeline
    if runs is not None:
        ordered = sorted(
            [_normalize_run(r) for r in runs],
            key=lambda r: (r.get("created_at") or r.get("run_id") or ""),
        )
    else:
        ordered = list(reversed(load_runs(500)))

    by_claim: dict[str, list] = {}
    last_verdict: dict[str, str | None] = {}
    for rec in ordered:
        for chk in rec.get("claim_checks") or []:
            cid = chk.get("claim_id")
            if not cid:
                continue
            verdict = chk.get("verdict")
            prev = last_verdict.get(cid)
            event = {
                "claim_id": cid,
                "run_id": rec.get("run_id"),
                "method_id": rec.get("method_id"),
                "verdict": verdict,
                "detail": chk.get("detail"),
                "previous": prev,
                "flipped": prev is not None and prev != verdict,
                "at": rec.get("created_at") or rec.get("run_id"),
            }
            by_claim.setdefault(cid, []).append(event)
            last_verdict[cid] = verdict

    # flatten with claim grouping metadata
    rows = []
    for cid, events in sorted(by_claim.items()):
        rows.append({
            "claim_id": cid,
            "events": events,
            "latest": events[-1]["verdict"] if events else None,
            "flips": sum(1 for e in events if e["flipped"]),
        })
    return rows


def evidence_brief_markdown(insights: dict | None = None) -> str:
    if insights is None:
        from app.insights import workspace_insights
        insights = workspace_insights()

    ev = insights.get("evidence") or {}
    cf = insights.get("conflicts") or {}
    ct = insights.get("counts") or {}
    stamp = insights.get("generated_at") or datetime.now(timezone.utc).isoformat()
    lines = [
        "# Verigraph evidence brief",
        "",
        f"_Generated {stamp}_",
        "",
        "## Coverage",
        "",
        f"- Papers: **{ct.get('papers', 0)}**",
        f"- Claims: **{ev.get('total_claims', 0)}** "
        f"({ev.get('validated', 0)} validated · {ev.get('refuted', 0)} refuted · {ev.get('untested', 0)} untested)",
        f"- Coverage: **{ev.get('coverage_pct', 0)}%**",
        f"- Runs: **{ct.get('runs', 0)}**",
        f"- Conflicts: **{cf.get('total', 0)}** "
        f"({cf.get('resolved', 0)} resolved · {cf.get('untested', 0)} untested)",
        "",
        "## Conflicts",
        "",
    ]
    rows = insights.get("conflict_rows") or []
    if not rows:
        lines.append("_No CONTRADICTS edges in this workspace._")
    else:
        for c in rows:
            lines.append(
                f"- **{c.get('from_claim')}** ({c.get('from_paper')}) ⇄ "
                f"**{c.get('to_claim')}** ({c.get('to_paper')}) — "
                f"`{c.get('status')}`: {c.get('summary')}"
            )
            lines.append(f"  - A: {c.get('from_verdict') or 'no runs yet'}")
            lines.append(f"  - B: {c.get('to_verdict') or 'no runs yet'}")
    lines += ["", "## Claims with evidence", ""]
    for row in insights.get("claim_rows") or []:
        if not row.get("verdict"):
            continue
        lines.append(
            f"- **{row.get('claim')}** ({row.get('paper')}): "
            f"`{row.get('verdict')}` via `{row.get('run_id')}`"
            + (f" — {row.get('detail')}" if row.get("detail") else "")
        )
    lines += [
        "",
        "---",
        "",
        "_Evidence is written only by deterministic curation after real executions._",
        "",
    ]
    return "\n".join(lines)


def load_impl_bundle() -> dict[str, str]:
    """method_id -> python source for cloud Daytona execution."""
    out: dict[str, str] = {}
    if not os.path.isdir(IMPL_DIR):
        return out
    for name in os.listdir(IMPL_DIR):
        if name.endswith(".py"):
            with open(os.path.join(IMPL_DIR, name)) as f:
                out[name[:-3]] = f.read()
    return out
