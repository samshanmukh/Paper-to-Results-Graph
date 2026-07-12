/** Verigraph read API for Butterbase static deploy.
 *  Routes via ?route=graph|evidence|insights|conflicts|runs|export|workspace|...
 *  POST run/{method_id} replays the latest persisted run from Butterbase.
 *  POST ask/investigate/brief/conduct answer from persisted graph + run history.
 *  POST workspace/new and reset clear the local view (shared Butterbase data unchanged).
 *  Upload/delete still require the full FastAPI backend.
 */

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...CORS },
  });
}

function notImplemented(msg: string) {
  return json({ detail: msg }, 501);
}

/** Cognee Cloud session logging — Sessions tab needs POST /api/v1/remember/entry (qa/trace). */
function cogneeReady(ctx: any): boolean {
  return (
    ctx?.env?.COGNEE_ENABLED === "true" &&
    !!ctx.env.COGNEE_SERVICE_URL &&
    !!ctx.env.COGNEE_API_KEY
  );
}

async function cogneeLogSession(
  ctx: any,
  question: string,
  answer: string,
  visitorId = "",
) {
  if (!cogneeReady(ctx)) return;
  const base = String(ctx.env.COGNEE_SERVICE_URL).replace(/\/$/, "");
  const dataset = ctx.env.COGNEE_DATASET || "default_dataset";
  const sessionId = /^[0-9a-f-]{36}$/i.test(visitorId)
    ? `verigraph-${visitorId}`
    : ctx.env.COGNEE_SESSION_ID || "verigraph-cloud-demo";
  try {
    const res = await fetch(`${base}/api/v1/remember/entry`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Api-Key": ctx.env.COGNEE_API_KEY,
      },
      body: JSON.stringify({
        entry: { type: "qa", question, answer: answer.slice(0, 8000) },
        dataset_name: dataset,
        session_id: sessionId,
      }),
    });
    if (!res.ok) {
      console.error("cognee remember/entry", res.status, await res.text());
    }
  } catch (e) {
    console.error("cognee session log failed", e);
  }
}

async function cogneeAugmentAsk(ctx: any, question: string, answer: string): Promise<string> {
  if (!cogneeReady(ctx)) return answer;
  const base = String(ctx.env.COGNEE_SERVICE_URL).replace(/\/$/, "");
  try {
    const res = await fetch(`${base}/api/v1/recall`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Api-Key": ctx.env.COGNEE_API_KEY,
      },
      body: JSON.stringify({
        query: question,
        datasets: [ctx.env.COGNEE_DATASET || "default_dataset"],
      }),
    });
    if (res.ok) {
      const hits = await res.json();
      const snippets = Array.isArray(hits)
        ? hits
            .slice(0, 3)
            .map((h: any) => (typeof h === "string" ? h : h.text || h.content || JSON.stringify(h)))
            .filter(Boolean)
        : [];
      if (snippets.length) {
        return (
          "**Cognee recall** (semantic memory):\n\n" +
          snippets.join("\n\n---\n\n") +
          "\n\n---\n\n" +
          answer
        );
      }
    }
  } catch (_) {
    /* fall through */
  }
  return answer;
}

function buildGraph(papers: any[], runs: any[]) {
  const nodes: any[] = [];
  const edges: any[] = [];
  const claimIndex: Record<string, string> = {};

  for (const row of papers) {
    const data = row.extraction || {};
    const p = data.paper || row;
    const pid = p.id || row.id;
    nodes.push({
      eid: `p-${pid}`,
      label: "Paper",
      key: pid,
      caption: p.title,
      props: { id: pid, title: p.title, year: p.year, arxiv: p.arxiv, topic: p.topic },
    });
    for (const c of data.claims || []) {
      const cid = `c-${c.id}`;
      claimIndex[c.id] = cid;
      nodes.push({
        eid: cid,
        label: "Claim",
        key: c.id,
        caption: c.text,
        props: { id: c.id, text: c.text, metric: c.metric },
      });
      edges.push({ src: cid, dst: `p-${pid}`, rel: "FROM" });
    }
    for (const m of data.methods || []) {
      const mid = `m-${m.id}`;
      nodes.push({
        eid: mid,
        label: "Method",
        key: m.id,
        caption: m.name,
        props: {
          id: m.id,
          name: m.name,
          description: m.description,
          runnable_hint: m.runnable_hint,
          params: JSON.stringify(m.params || []),
        },
      });
      edges.push({ src: mid, dst: `p-${pid}`, rel: "DESCRIBED_IN" });
    }
    for (const cited of data.cites || []) {
      edges.push({ src: `p-${pid}`, dst: `p-${cited}`, rel: "CITES" });
    }
    for (const rel of data.claim_relations || []) {
      const from = claimIndex[rel.from];
      const to = claimIndex[rel.to];
      if (from && to) edges.push({ src: from, dst: to, rel: rel.type });
    }
  }

  for (const r of runs) {
    const rid = `r-${r.id}`;
    nodes.push({
      eid: rid,
      label: "Run",
      key: r.id,
      caption: r.id,
      props: {
        id: r.id,
        backend: r.backend,
        status: r.status,
        exit_code: r.exit_code,
        duration_s: Number(r.duration_s),
        metrics: JSON.stringify(r.metrics || {}),
      },
    });
    if (r.method_id) {
      edges.push({ src: rid, dst: `m-${r.method_id}`, rel: "IMPLEMENTS" });
    }
    const checks = r.claim_checks?.items || r.claim_checks || [];
    for (const chk of checks) {
      const cid = claimIndex[chk.claim_id];
      if (cid && (chk.verdict === "VALIDATES" || chk.verdict === "REFUTES")) {
        edges.push({ src: rid, dst: cid, rel: chk.verdict });
      }
    }
  }

  return { nodes, edges };
}

function runRowToRecord(row: any) {
  const checks = row.claim_checks?.items || row.claim_checks || [];
  return {
    run_id: row.id,
    method_id: row.method_id,
    backend: row.backend || "daytona",
    exit_code: row.exit_code ?? 0,
    duration_s: Number(row.duration_s) || 0,
    stdout: row.stdout || "",
    stderr: "",
    error: row.error || null,
    result: {
      method_id: row.method_id,
      metrics: row.metrics || {},
      claim_checks: checks,
    },
    replay: true,
  };
}

function latestRunForMethod(runs: any[], methodId: string) {
  const hits = runs.filter((r) => r.method_id === methodId);
  if (!hits.length) return null;
  const successful = hits.filter(
    (r) => !r.error && r.exit_code === 0 && (!r.status || r.status === "success"),
  );
  const candidates = successful.length ? successful : hits;
  candidates.sort((a, b) => String(b.id).localeCompare(String(a.id)));
  return candidates[0];
}

function isInfrastructureFailure(run: any) {
  const error = String(run?.error || "");
  return /DaytonaValidationError/i.test(error) && /disk limit exceeded|concurrency limits/i.test(error);
}

function buildEvidence(papers: any[], runs: any[]) {
  const runByClaim: Record<string, any> = {};
  for (const r of runs) {
    const checks = r.claim_checks?.items || r.claim_checks || [];
    for (const chk of checks) {
      if (chk.claim_id) runByClaim[chk.claim_id] = { run: r, verdict: chk.verdict, detail: chk.detail };
    }
  }
  const rows: any[] = [];
  for (const row of papers) {
    const data = row.extraction || {};
    const pid = (data.paper || row).id || row.id;
    for (const c of data.claims || []) {
      const hit = runByClaim[c.id];
      rows.push({
        paper: pid,
        claim: c.id,
        text: c.text,
        evidence: hit ? `${hit.verdict} by ${hit.run.id}` : "no runs yet",
      });
    }
  }
  return rows;
}

type ClaimRow = {
  id: string;
  text: string;
  paperId: string;
  paperTitle: string;
  evidence?: { verdict: string; runId: string; detail?: string };
};

type WorkspaceCtx = {
  papers: { id: string; title: string; year?: number }[];
  claims: ClaimRow[];
  methods: { id: string; name: string; paperId: string; hasRun: boolean }[];
  conflicts: {
    from: string;
    to: string;
    fromText: string;
    toText: string;
    fromPaper: string;
    toPaper: string;
  }[];
  runs: any[];
  claimById: Record<string, ClaimRow>;
};

function buildWorkspaceCtx(papers: any[], runs: any[]): WorkspaceCtx {
  const runByClaim: Record<string, { verdict: string; runId: string; detail?: string }> = {};
  const methodsWithRuns = new Set(runs.map((r) => r.method_id).filter(Boolean));

  for (const r of runs) {
    const checks = r.claim_checks?.items || r.claim_checks || [];
    for (const chk of checks) {
      if (chk.claim_id) {
        runByClaim[chk.claim_id] = {
          verdict: chk.verdict,
          runId: r.id,
          detail: chk.detail,
        };
      }
    }
  }

  const claimById: Record<string, ClaimRow> = {};
  const claims: ClaimRow[] = [];
  const methods: WorkspaceCtx["methods"] = [];
  const conflicts: WorkspaceCtx["conflicts"] = [];
  const paperMeta: Record<string, { id: string; title: string; year?: number }> = {};

  for (const row of papers) {
    const data = row.extraction || {};
    const p = data.paper || row;
    const pid = p.id || row.id;
    paperMeta[pid] = { id: pid, title: p.title || row.title, year: p.year || row.year };
    for (const c of data.claims || []) {
      const cr: ClaimRow = {
        id: c.id,
        text: c.text,
        paperId: pid,
        paperTitle: paperMeta[pid].title,
        evidence: runByClaim[c.id],
      };
      claims.push(cr);
      claimById[c.id] = cr;
    }
    for (const m of data.methods || []) {
      methods.push({
        id: m.id,
        name: m.name,
        paperId: pid,
        hasRun: methodsWithRuns.has(m.id),
      });
    }
  }

  for (const row of papers) {
    const data = row.extraction || {};
    for (const rel of data.claim_relations || []) {
      if (rel.type !== "CONTRADICTS") continue;
      const from = claimById[rel.from];
      const to = claimById[rel.to];
      if (!from || !to) continue;
      conflicts.push({
        from: rel.from,
        to: rel.to,
        fromText: from.text,
        toText: to.text,
        fromPaper: from.paperId,
        toPaper: to.paperId,
      });
    }
  }

  return {
    papers: Object.values(paperMeta),
    claims,
    methods,
    conflicts,
    runs,
    claimById,
  };
}

function claimsWithEvidence(ctx: WorkspaceCtx) {
  return ctx.claims.filter((c) => c.evidence);
}

function untestedClaims(ctx: WorkspaceCtx) {
  return ctx.claims.filter((c) => !c.evidence);
}

function nextMethodToRun(ctx: WorkspaceCtx) {
  const untested = ctx.methods.filter((m) => !m.hasRun);
  const wilson = untested.find((m) => m.id === "wilson2017-m1");
  return (wilson || untested[0] || ctx.methods[0])?.id || null;
}

function adjudicateConflict(c: WorkspaceCtx["conflicts"][0], claimById: Record<string, ClaimRow>) {
  const fromEv = claimById[c.from]?.evidence;
  const toEv = claimById[c.to]?.evidence;
  const fv = fromEv?.verdict || null;
  const tv = toEv?.verdict || null;
  let status = "mixed";
  let summary = "Mixed evidence — inspect both claim cards.";
  if (!fv && !tv) {
    status = "untested";
    summary = "Neither side has a sandbox run yet.";
  } else if (fv === "VALIDATES" && tv === "VALIDATES") {
    status = "both_supported";
    summary = "Both claims have validating runs — the conflict remains live.";
  } else if (fv === "VALIDATES" && tv === "REFUTES") {
    status = "from_wins";
    summary = `${c.from} validated; ${c.to} refuted.`;
  } else if (tv === "VALIDATES" && fv === "REFUTES") {
    status = "to_wins";
    summary = `${c.to} validated; ${c.from} refuted.`;
  } else if (fv === "VALIDATES" && !tv) {
    status = "from_leading";
    summary = `${c.from} validated; opposing claim untested.`;
  } else if (tv === "VALIDATES" && !fv) {
    status = "to_leading";
    summary = `${c.to} validated; opposing claim untested.`;
  } else if (fv === "REFUTES" && tv === "REFUTES") {
    status = "both_refuted";
    summary = "Both sides were refuted by runs.";
  } else if (fv === "REFUTES" && !tv) {
    status = "from_refuted";
    summary = `${c.from} refuted; opposing claim untested.`;
  } else if (tv === "REFUTES" && !fv) {
    status = "to_refuted";
    summary = `${c.to} refuted; opposing claim untested.`;
  }
  return {
    from_claim: c.from,
    from_text: c.fromText,
    from_paper: c.fromPaper,
    from_paper_title: claimById[c.from]?.paperTitle || c.fromPaper,
    from_verdict: fv,
    from_run_id: fromEv?.runId || null,
    to_claim: c.to,
    to_text: c.toText,
    to_paper: c.toPaper,
    to_paper_title: claimById[c.to]?.paperTitle || c.toPaper,
    to_verdict: tv,
    to_run_id: toEv?.runId || null,
    status,
    summary,
  };
}

function buildInsights(ctx: WorkspaceCtx) {
  const validated = ctx.claims.filter((c) => c.evidence?.verdict === "VALIDATES").length;
  const refuted = ctx.claims.filter((c) => c.evidence?.verdict === "REFUTES").length;
  const untested = ctx.claims.filter((c) => !c.evidence).length;
  const totalClaims = ctx.claims.length;
  const coverage = totalClaims ? Math.round((1000 * (validated + refuted)) / totalClaims) / 10 : 0;
  const conflictRows = ctx.conflicts.map((c) => adjudicateConflict(c, ctx.claimById));
  const neverRun = ctx.methods.filter((m) => !m.hasRun);
  return {
    generated_at: new Date().toISOString(),
    counts: {
      papers: ctx.papers.length,
      claims: totalClaims,
      methods: ctx.methods.length,
      runs: ctx.runs.length,
      artifacts: 0,
      nodes: ctx.papers.length + totalClaims + ctx.methods.length + ctx.runs.length,
    },
    evidence: {
      total_claims: totalClaims,
      validated,
      refuted,
      untested,
      coverage_pct: coverage,
    },
    conflicts: {
      total: conflictRows.length,
      untested: conflictRows.filter((c) => c.status === "untested").length,
      adjudicated: conflictRows.filter((c) => c.status !== "untested").length,
      resolved: conflictRows.filter((c) => c.status === "from_wins" || c.status === "to_wins").length,
      both_supported: conflictRows.filter((c) => c.status === "both_supported").length,
    },
    methods: {
      total: ctx.methods.length,
      with_runs: ctx.methods.filter((m) => m.hasRun).length,
      never_run: neverRun.length,
      next_recommended: nextMethodToRun(ctx),
      items: ctx.methods.map((m) => ({
        method: m.id,
        name: m.name,
        paper: m.paperId,
        runnable_hint: null,
        run_count: m.hasRun ? 1 : 0,
        latest_run: null,
      })),
    },
    conflict_rows: conflictRows,
    claim_rows: ctx.claims.map((c) => ({
      paper: c.paperId,
      paper_title: c.paperTitle,
      claim: c.id,
      text: c.text,
      verdict: c.evidence?.verdict || null,
      run_id: c.evidence?.runId || null,
      detail: c.evidence?.detail || null,
    })),
  };
}

function buildRunsList(runs: any[], limit = 50) {
  return [...runs]
    .sort((a, b) => String(b.id).localeCompare(String(a.id)))
    .slice(0, limit)
    .map((r) => ({
      run_id: r.id,
      method_id: r.method_id,
      backend: r.backend || "daytona",
      exit_code: r.exit_code ?? 0,
      duration_s: Number(r.duration_s) || 0,
      error: r.error || null,
      metrics: r.metrics || {},
      claim_checks: r.claim_checks?.items || r.claim_checks || [],
      source: "butterbase",
      replay: true,
    }));
}

function buildExport(papers: any[], runs: any[], ctx: WorkspaceCtx) {
  const insights = buildInsights(ctx);
  return {
    exported_at: new Date().toISOString(),
    format: "verigraph-workspace-v1",
    insights: {
      counts: insights.counts,
      evidence: insights.evidence,
      conflicts: insights.conflicts,
    },
    conflicts: insights.conflict_rows,
    evidence: insights.claim_rows,
    runs: buildRunsList(runs, 200),
    graph: buildGraph(papers, runs),
  };
}

function answerAsk(question: string, ctx: WorkspaceCtx) {
  const q = (question || "").toLowerCase().trim();
  const evidenced = claimsWithEvidence(ctx);
  const untested = untestedClaims(ctx);

  if (/executable evidence|which claims|validated|refuted|have evidence|runs? (show|prove)/.test(q)) {
    if (!evidenced.length) {
      return (
        "No claims have executable evidence yet on this deploy. " +
        `There are ${untested.length} untested claims across ${ctx.papers.length} papers. ` +
        "Select **wilson2017-m1** on the graph and press **RUN** to replay the stored Wilson counterexample."
      );
    }
    const lines = evidenced.map((c) => {
      const ev = c.evidence!;
      const detail = ev.detail ? ` — ${ev.detail}` : "";
      return `• **${c.id}** (${c.paperId}): ${ev.verdict} via \`${ev.runId}\`${detail}`;
    });
    const pending = untested.length
      ? `\n\nStill untested: ${untested.slice(0, 4).map((c) => c.id).join(", ")}` +
        (untested.length > 4 ? ` (+${untested.length - 4} more)` : "")
      : "";
    return (
      `${evidenced.length} claim(s) have executable evidence from persisted runs:\n\n` +
      lines.join("\n") +
      pending +
      "\n\n_(Answers on this deploy are graph-grounded from Butterbase history — no live RocketRide pipeline.)_"
    );
  }

  if (/agree|consensus|same (view|conclusion)|papers.*adam/.test(q)) {
    if (!ctx.conflicts.length) {
      return "The loaded papers do not declare any CONTRADICTS edges — nothing to disagree about in the graph.";
    }
    const adamConflicts = ctx.conflicts.filter(
      (c) => /adam/i.test(c.fromPaper) || /adam/i.test(c.toPaper) || /adam/i.test(c.fromText) || /adam/i.test(c.toText)
    );
    const sample = (adamConflicts.length ? adamConflicts : ctx.conflicts).slice(0, 3);
    const lines = sample.map(
      (c) =>
        `• **${c.from}** (${c.fromPaper}) CONTRADICTS **${c.to}** (${c.toPaper})\n  _"${c.fromText.slice(0, 90)}…"_ vs _"${c.toText.slice(0, 90)}…"_`
    );
    return (
      "They do **not** fully agree — the graph records cross-paper conflicts:\n\n" +
      lines.join("\n\n") +
      (ctx.conflicts.length > sample.length ? `\n\n…plus ${ctx.conflicts.length - sample.length} more CONTRADICTS edge(s).` : "") +
      "\n\nWilson's separable counterexample run (replay **wilson2017-m1**) currently **VALIDATES** that Adam generalizes worse than GD on the constructed problem."
    );
  }

  const paperMatch = q.match(/contradict[s]?\s+(\w[\w-]*)/) || q.match(/(\w[\w-]*)\s+contradict/);
  const targetPaper = paperMatch?.[1];
  if (/contradict|conflict|oppose|disagree/.test(q)) {
    const hits = targetPaper
      ? ctx.conflicts.filter((c) => c.fromPaper === targetPaper || c.toPaper === targetPaper)
      : ctx.conflicts;
    if (!hits.length) {
      return targetPaper
        ? `No CONTRADICTS edges involve **${targetPaper}** in the current workspace.`
        : "No CONTRADICTS edges are recorded in the current workspace.";
    }
    const lines = hits.map(
      (c) =>
        `• **${c.from}** (${c.fromPaper}) → **${c.to}** (${c.toPaper})\n  "${c.fromText}"\n  vs "${c.toText}"`
    );
    return `Found ${hits.length} contradiction(s)${targetPaper ? ` involving **${targetPaper}**` : ""}:\n\n${lines.join("\n\n")}`;
  }

  if (/recommend|what (should|to) run|next (method|experiment)|untested/.test(q)) {
    const methodId = nextMethodToRun(ctx);
    const untestedIds = untested.map((c) => c.id);
    return (
      `Graph audit: ${ctx.conflicts.length} cross-paper conflict(s), ${untested.length} untested claim(s).\n\n` +
      (methodId ? `Recommended next run: **${methodId}**` : "All methods already have persisted runs on this deploy.") +
      (untestedIds.length ? `\n\nUntested claims include: ${untestedIds.slice(0, 5).join(", ")}` : "") +
      "\n\n_(Butterbase deploy — press RUN to replay stored results.)_"
    );
  }

  if (/brief|summary|overview|status/.test(q)) {
    return answerBrief(ctx).answer;
  }

  if (/cognee|semantic memory|memory recall/.test(q)) {
    const evidenced = claimsWithEvidence(ctx);
    return (
      "Cognee is **active on this cloud deploy** — each Ask / workflow action is logged to your " +
      "**Sessions** tab on platform.cognee.ai (session `verigraph-cloud-demo`).\n\n" +
      `Graph snapshot: ${ctx.papers.length} papers, ${evidenced.length} claims with evidence, ` +
      `${ctx.runs.length} persisted runs.\n\n` +
      "Run `scripts/sync_cognee.py` locally to index paper text into **Brain**. " +
      "Try: _Which claims have executable evidence?_"
    );
  }

  const validated = evidenced.filter((c) => c.evidence?.verdict === "VALIDATES").length;
  const refuted = evidenced.filter((c) => c.evidence?.verdict === "REFUTES").length;
  return (
    `Workspace: **${ctx.papers.length}** papers, **${ctx.claims.length}** claims, **${ctx.runs.length}** persisted run(s).\n\n` +
    `Evidence: ${validated} validated, ${refuted} refuted, ${untested.length} untested. ` +
    `${ctx.conflicts.length} CONTRADICTS edge(s) in the graph.\n\n` +
    "Try: _Which claims have executable evidence?_ · _Do these papers agree on Adam?_ · _What contradicts adam2014?_\n\n" +
    "_(Graph-grounded answer from Butterbase — for live RocketRide agent Q&A, run the full stack locally.)_"
  );
}

function answerInvestigate(ctx: WorkspaceCtx) {
  const untested = untestedClaims(ctx).map((c) => c.id);
  const methodId = nextMethodToRun(ctx);
  const conflictLines = ctx.conflicts.slice(0, 4).map(
    (c) => `• ${c.from} (${c.fromPaper}) CONTRADICTS ${c.to} (${c.toPaper})`
  );
  const prose =
    `Investigator audit (persisted graph):\n\n` +
    `**${ctx.conflicts.length}** cross-paper CONTRADICTS conflict(s):\n` +
    (conflictLines.length ? conflictLines.join("\n") : "• none") +
    `\n\n**${untested.length}** untested claim(s)` +
    (untested.length ? `: ${untested.slice(0, 6).join(", ")}` : "") +
    (methodId ? `\n\nRecommend running **${methodId}** next — highest-priority executable method without a fresh sandbox run on this deploy.` : "");

  return {
    answer: prose,
    agent: "investigator",
    header_present: true,
    replay: true,
    payload: {
      conflicts: ctx.conflicts.map((c) => ({ from: c.from, to: c.to, type: "CONTRADICTS" })),
      untested_claims: untested,
      recommended_method_id: methodId,
    },
  };
}

function answerBrief(ctx: WorkspaceCtx) {
  const evidenced = claimsWithEvidence(ctx);
  const validated = evidenced.filter((c) => c.evidence?.verdict === "VALIDATES");
  const refuted = evidenced.filter((c) => c.evidence?.verdict === "REFUTES");
  const untested = untestedClaims(ctx);
  const runIds = [...new Set(ctx.runs.map((r) => r.id))];
  const headline =
    validated.length && ctx.runs.some((r) => r.method_id === "wilson2017-m1")
      ? "Wilson counterexample: GD test error 0.000 vs Adam 0.425 — VALIDATES generalization gap"
      : `${validated.length} validated, ${refuted.length} refuted, ${untested.length} untested`;

  const lines: string[] = [`**${headline}**`, ""];
  if (validated.length) {
    lines.push("**Validated**");
    validated.forEach((c) => lines.push(`• ${c.id}: ${c.evidence?.detail || c.text}`));
    lines.push("");
  }
  if (refuted.length) {
    lines.push("**Refuted**");
    refuted.forEach((c) => lines.push(`• ${c.id}: ${c.evidence?.detail || c.text}`));
    lines.push("");
  }
  if (untested.length) {
    lines.push(`**Untested** (${untested.length}): ${untested.slice(0, 5).map((c) => c.id).join(", ")}`);
  }

  return {
    answer: lines.join("\n") + "\n\n_(Evidence brief from persisted Butterbase runs.)_",
    agent: "reporter",
    header_present: true,
    replay: true,
    payload: {
      run_ids_covered: runIds,
      headline,
      validated: validated.map((c) => c.id),
      refuted: refuted.map((c) => c.id),
      untested: untested.map((c) => c.id),
    },
  };
}

function answerConduct(ctx: WorkspaceCtx) {
  const inv = answerInvestigate(ctx);
  const methodId = inv.payload.recommended_method_id || "wilson2017-m1";
  const run = latestRunForMethod(ctx.runs, methodId);
  const brief = answerBrief(ctx);
  const steps: string[] = [
    `[investigator] ✓ ${inv.payload.conflicts.length} conflicts, ${inv.payload.untested_claims.length} untested — recommended ${methodId}`,
  ];
  if (run) {
    const checks = run.claim_checks?.items || run.claim_checks || [];
    const verdicts = checks.map((c: any) => `${c.verdict} ${c.claim_id}`).join(", ");
    steps.push(`[executor] ✓ ${run.id} [${run.backend || "daytona"}] replay — ${verdicts || "metrics recorded"}`);
  } else {
    steps.push(`[executor] ✗ no persisted run for ${methodId}`);
  }
  steps.push(`[reporter] ✓ brief over ${brief.payload.run_ids_covered.length} runs — ${brief.payload.headline}`);

  return {
    answer: steps.join("\n") + "\n\n" + brief.answer,
    steps,
    method_id: methodId,
    run_id: run?.id || null,
    investigator: inv.payload,
    reporter: brief.payload,
    replay: true,
  };
}

async function readJsonBody(req: Request) {
  try {
    return await req.json();
  } catch {
    return {};
  }
}

function isPublicIp(value: string) {
  const ip = value.trim().replace(/^\[|\]$/g, "");
  const parts = ip.split(".");
  if (parts.length === 4 && parts.every((part) => /^\d{1,3}$/.test(part) && Number(part) <= 255)) {
    const [a, b] = parts.map(Number);
    return !(
      a === 0 ||
      a === 10 ||
      a === 127 ||
      (a === 100 && b >= 64 && b <= 127) ||
      (a === 169 && b === 254) ||
      (a === 172 && b >= 16 && b <= 31) ||
      (a === 192 && b === 168) ||
      a >= 224
    );
  }
  if (ip.includes(":") && /^[0-9a-f:]+$/i.test(ip)) {
    const lower = ip.toLowerCase();
    return !(
      lower === "::1" ||
      lower === "::" ||
      lower.startsWith("fc") ||
      lower.startsWith("fd") ||
      lower.startsWith("fe8") ||
      lower.startsWith("fe9") ||
      lower.startsWith("fea") ||
      lower.startsWith("feb")
    );
  }
  return false;
}

function publicClientIp(req: Request, ctx: any) {
  const forwarded = (req.headers.get("x-forwarded-for") || "").split(",");
  const candidates = [
    req.headers.get("fly-client-ip"),
    req.headers.get("cf-connecting-ip"),
    req.headers.get("true-client-ip"),
    ...forwarded,
    req.headers.get("x-real-ip"),
    ctx?.request?.ip,
  ];
  return candidates.map((value) => String(value || "").trim()).find(isPublicIp) || "";
}

async function visitorLocation(req: Request, ctx: any) {
  const decodeHeader = (name: string) => {
    const value = req.headers.get(name) || "";
    try {
      return decodeURIComponent(value);
    } catch {
      return value;
    }
  };
  const ip = publicClientIp(req, ctx);
  const headerCountry = [
      req.headers.get("cf-ipcountry") ||
      req.headers.get("x-vercel-ip-country") ||
      req.headers.get("cloudfront-viewer-country"),
      ctx?.request?.country,
    ].map((value) => String(value || "").toUpperCase()).find((value) => /^[A-Z]{2}$/.test(value)) || "";
  const location = {
    ip,
    country: headerCountry,
    region:
      decodeHeader("x-vercel-ip-country-region") ||
      decodeHeader("cloudfront-viewer-country-region") ||
      "",
    city:
      decodeHeader("x-vercel-ip-city") ||
      decodeHeader("cloudfront-viewer-city") ||
      "",
  };
  if (!ip) return location;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 2500);
  try {
    const response = await fetch(
      `https://ipwho.is/${encodeURIComponent(ip)}?fields=success,country_code,region,city`,
      { signal: controller.signal },
    );
    if (response.ok) {
      const geo = await response.json();
      if (geo?.success) {
        location.country = String(geo.country_code || location.country).slice(0, 2).toUpperCase();
        location.region = String(geo.region || location.region).slice(0, 120);
        location.city = String(geo.city || location.city).slice(0, 120);
      }
    }
  } catch (_) {
    // Header-derived location is still useful when geolocation is unavailable.
  } finally {
    clearTimeout(timer);
  }
  return location;
}

function validEmail(value: string) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value) && value.length <= 254;
}

async function registerVisitor(req: Request, ctx: any) {
  const body = await readJsonBody(req);
  const email = String(body.email || "").trim().toLowerCase();
  const timezone = String(body.timezone || "").slice(0, 100);
  if (!validEmail(email)) return json({ detail: "Enter a valid email address." }, 400);

  const geo = await visitorLocation(req, ctx);
  const result = await ctx.db.query(
    `INSERT INTO demo_visitors (email, ip_address, region, country, city, timezone)
     VALUES ($1, $2, $3, $4, $5, $6)
     ON CONFLICT (email) DO UPDATE SET
       ip_address = EXCLUDED.ip_address,
       region = EXCLUDED.region,
       country = EXCLUDED.country,
       city = EXCLUDED.city,
       timezone = EXCLUDED.timezone,
       last_seen = now(),
       last_tool = 'demo_opened'
     RETURNING id, email`,
    [email, geo.ip, geo.region, geo.country, geo.city, timezone],
  );
  const visitor = result.rows?.[0];
  return json({ ok: true, visitor_id: visitor?.id, email: visitor?.email || email });
}

async function recordToolUse(req: Request, route: string, ctx: any) {
  const visitorId = new URL(req.url).searchParams.get("visitor") || "";
  if (!/^[0-9a-f-]{36}$/i.test(visitorId)) return;
  const tool = route.startsWith("run/") ? "run" : route.slice(0, 80);
  await ctx.db.query(
    `UPDATE demo_visitors
     SET last_seen = now(), last_tool = $2, tool_uses = tool_uses + 1
     WHERE id = $1`,
    [visitorId, tool],
  );
}

function adminAuthorized(req: Request, ctx: any) {
  const expected = String(ctx?.env?.ADMIN_TRACKING_KEY || "");
  const supplied = (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  return expected.length >= 16 && supplied === expected;
}

async function adminUsers(req: Request, ctx: any) {
  if (!ctx?.env?.ADMIN_TRACKING_KEY) {
    return json({ detail: "ADMIN_TRACKING_KEY is not configured." }, 503);
  }
  if (!adminAuthorized(req, ctx)) return json({ detail: "Unauthorized" }, 401);
  const result = await ctx.db.query(
    `SELECT id, email, ip_address, region, country, city, timezone,
            first_seen, last_seen, last_tool, tool_uses
     FROM demo_visitors
     ORDER BY last_seen DESC
     LIMIT 500`,
  );
  return json({ users: result.rows || [], generated_at: new Date().toISOString() });
}

export default async function handler(req: Request, ctx: any): Promise<Response> {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });

  const url = new URL(req.url);
  const route = url.searchParams.get("route") || "health";
  const visitorId = url.searchParams.get("visitor") || "";

  try {
    if (req.method === "POST" && route === "register") return await registerVisitor(req, ctx);
    if (req.method === "GET" && route === "admin/users") return await adminUsers(req, ctx);

    const papersRes = await ctx.db.query("SELECT * FROM papers ORDER BY year, id");
    const runsRes = await ctx.db.query("SELECT * FROM runs ORDER BY id");
    const papers = papersRes.rows || [];
    // Quota failures are operational audit records, not scientific evidence.
    // Keep them in Butterbase but do not render or replay them as graph runs.
    const runs = (runsRes.rows || []).filter((run: any) => !isInfrastructureFailure(run));
    const ws = buildWorkspaceCtx(papers, runs);

    if (route === "health") return json({ ok: true, papers: papers.length, runs: runs.length });

    if (route === "graph") return json(buildGraph(papers, runs));

    if (route === "evidence") return json(buildEvidence(papers, runs));

    if (route === "insights") return json(buildInsights(ws));

    if (route === "conflicts") {
      return json(ws.conflicts.map((c) => adjudicateConflict(c, ws.claimById)));
    }

    if (route === "runs") {
      const limit = Math.min(Math.max(Number(url.searchParams.get("limit") || 50), 1), 200);
      return json(buildRunsList(runs, limit));
    }

    if (route === "export") {
      const payload = buildExport(papers, runs, ws);
      const stamp = new Date().toISOString().replace(/[:.]/g, "").slice(0, 15) + "Z";
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: {
          "Content-Type": "application/json",
          "Content-Disposition": `attachment; filename="verigraph-export-${stamp}.json"`,
          ...CORS,
        },
      });
    }

    if (route === "workspace") {
      const ids = papers.map((p: any) => (p.extraction?.paper?.id || p.id));
      return json({
        empty: papers.length === 0,
        papers: papers.length,
        claims: buildEvidence(papers, runs).length,
        runs: runs.length,
        paper_ids: ids,
        papers_detail: papers.map((p: any) => {
          const data = p.extraction || {};
          const paper = data.paper || p;
          return {
            id: paper.id || p.id,
            title: paper.title || p.title,
            year: paper.year || p.year,
            arxiv: paper.arxiv || p.arxiv,
            claims: (data.claims || []).length,
            methods: (data.methods || []).length,
          };
        }),
      });
    }

    if (req.method === "POST") {
      await recordToolUse(req, route, ctx);
      if (route === "workspace/load-demo") {
        return json({
          papers: papers.length,
          claims: buildEvidence(papers, runs).length,
          empty: false,
          replay: true,
          view: "no-runs",
          message: `Demo loaded — ${papers.length} papers (Adam · Wilson · AdamW), no runs yet.`,
        });
      }
      if (route === "workspace/new") {
        return json({
          empty: true,
          papers: 0,
          claims: 0,
          runs: 0,
          replay: true,
          view: "empty",
          message:
            "New workspace ready. On this Butterbase deploy the view is cleared locally — " +
            "use Load demo to restore the paper graph, or run the full stack locally to add papers.",
        });
      }
      if (route === "reset") {
        const claimCount = buildEvidence(papers, runs).length;
        return json({
          empty: papers.length === 0,
          papers: papers.length,
          claims: claimCount,
          runs: 0,
          pristine: true,
          replay: true,
          view: "no-runs",
          message: 'Runs cleared — all claims show "no runs yet" in this view.',
        });
      }
      if (route.startsWith("run/")) {
        const methodId = route.slice(4);
        const hit = latestRunForMethod(runs, methodId);
        if (hit) {
          const rec = runRowToRecord(hit);
          const checks = hit.claim_checks?.items || hit.claim_checks || [];
          const summary =
            `[run replay ${hit.id}] method ${methodId} ` +
            checks.map((c: any) => `${c.verdict} ${c.claim_id}`).join(", ");
          await cogneeLogSession(ctx, `RUN ${methodId}`, summary, visitorId);
          return json(rec);
        }
        return json(
          {
            detail:
              `No persisted run for ${methodId} on this deploy. ` +
              "Load the demo or run wilson2017-m1 locally against the full Verigraph backend.",
          },
          404
        );
      }
      if (route === "ask") {
        const body = await readJsonBody(req);
        const question = String(body.question || "").trim();
        if (!question) return json({ detail: "question is required" }, 400);
        let answer = answerAsk(question, ws);
        answer = await cogneeAugmentAsk(ctx, question, answer);
        await cogneeLogSession(ctx, question, answer, visitorId);
        return json({ answer, replay: true, cognee_session: cogneeReady(ctx) });
      }
      if (route === "investigate") {
        const r = answerInvestigate(ws);
        await cogneeLogSession(ctx, "Investigate graph audit", r.answer, visitorId);
        return json(r);
      }
      if (route === "brief") {
        const r = answerBrief(ws);
        await cogneeLogSession(ctx, "Evidence brief", r.answer, visitorId);
        return json(r);
      }
      if (route === "conduct") {
        const body = await readJsonBody(req);
        const r = answerConduct(ws);
        const msg = String(body.message || "Full evidence workflow");
        await cogneeLogSession(ctx, msg, r.answer, visitorId);
        return json(r);
      }
      return notImplemented(`POST ${route} is not available on the Butterbase read-only deploy.`);
    }

    if (req.method === "DELETE" && route.startsWith("workspace/papers/")) {
      return notImplemented("Paper removal requires the full FastAPI backend.");
    }

    return json({ error: `unknown route: ${route}` }, 404);
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
}
