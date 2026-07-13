/** Verigraph read API for Butterbase static deploy.
 *  Routes via ?route=graph|evidence|workspace|...
 *  POST run/{method_id} replays the latest persisted run from Butterbase.
 *  POST ask/investigate/brief/conduct answer from persisted graph + run history.
 *  Default reads are scoped by the authoritative workspace_state snapshot.
 *  POST workspace/new and reset can additionally clear a visitor's local view.
 *  Upload/delete still require the full FastAPI backend.
 */

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...CORS },
  });
}

function notImplemented(msg: string) {
  return json({ detail: msg }, 501);
}

class HttpError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Cognee Cloud session logging — Sessions tab needs POST /api/v1/remember/entry (qa/trace). */
function cogneeReady(ctx: any): boolean {
  return (
    ctx?.env?.COGNEE_ENABLED === "true" &&
    !!ctx.env.COGNEE_SERVICE_URL &&
    !!ctx.env.COGNEE_API_KEY
  );
}

function cogneeSessionId(ctx: any, visitorId = ""): string {
  return UUID_PATTERN.test(visitorId)
    ? `verigraph-${visitorId}`
    : ctx?.env?.COGNEE_SESSION_ID || "verigraph-cloud-demo";
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
  const sessionId = cogneeSessionId(ctx, visitorId);
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
  }

  // Claim targets can belong to a paper sorted later, so relations need a
  // second pass after the complete claim index has been built.
  for (const row of papers) {
    const data = row.extraction || {};
    for (const rel of data.claim_relations || []) {
      const from = claimIndex[rel.from];
      const to = claimIndex[rel.to];
      if (from && to && (rel.type === "SUPPORTS" || rel.type === "CONTRADICTS")) {
        edges.push({ src: from, dst: to, rel: rel.type });
      }
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
        implementation_source: implementationSource(r),
        implementation_fingerprint: r.implementation_fingerprint || null,
        context_digest: r.context_digest || null,
        provisional: isProvisionalRun(r),
      },
    });
    if (r.method_id) {
      edges.push({ src: rid, dst: `m-${r.method_id}`, rel: "IMPLEMENTS" });
    }
    const checks = isSuccessfulRun(r) ? (r.claim_checks?.items || r.claim_checks || []) : [];
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
  const params = row.params && typeof row.params === "object" && !Array.isArray(row.params)
    ? row.params
    : {};
  const parameterOverrides =
    row.parameter_overrides &&
    typeof row.parameter_overrides === "object" &&
    !Array.isArray(row.parameter_overrides)
      ? row.parameter_overrides
      : {};
  return {
    run_id: row.id,
    method_id: row.method_id,
    backend: row.backend || "daytona",
    exit_code: row.exit_code ?? 0,
    duration_s: Number(row.duration_s) || 0,
    created_at: row.created_at || null,
    implementation_source: implementationSource(row),
    implementation_fingerprint: row.implementation_fingerprint || null,
    context_digest: row.context_digest || null,
    workspace_revision: row.workspace_revision ?? null,
    provisional: isProvisionalRun(row),
    params,
    parameter_overrides: parameterOverrides,
    stdout: row.stdout || "",
    stderr: "",
    error: row.error || null,
    result: {
      method_id: row.method_id,
      params,
      metrics: row.metrics || {},
      claim_checks: checks,
    },
    replay: true,
  };
}

function implementationSource(run: any): "curated" | "llm" | "unknown" {
  return run?.implementation_source === "curated" || run?.implementation_source === "llm"
    ? run.implementation_source
    : "unknown";
}

function isProvisionalRun(run: any): boolean {
  return (
    implementationSource(run) !== "curated" ||
    !/^[0-9a-f]{64}$/.test(String(run?.implementation_fingerprint || "")) ||
    !/^[0-9a-f]{64}$/.test(String(run?.context_digest || "")) ||
    run?.provisional === true
  );
}

function isSuccessfulRun(run: any): boolean {
  return !run?.error && Number(run?.exit_code) === 0 && (!run?.status || run.status === "success");
}

function compareRunsNewestFirst(a: any, b: any): number {
  const aTime = Date.parse(String(a?.created_at || ""));
  const bTime = Date.parse(String(b?.created_at || ""));
  if (Number.isFinite(aTime) && Number.isFinite(bTime) && aTime !== bTime) return bTime - aTime;
  if (Number.isFinite(aTime) !== Number.isFinite(bTime)) return Number.isFinite(bTime) ? 1 : -1;
  return String(b?.id || "").localeCompare(String(a?.id || ""));
}

function successfulRunsNewestFirst(runs: any[]): any[] {
  return runs.filter(isSuccessfulRun).slice().sort(compareRunsNewestFirst);
}

function latestRunForMethod(runs: any[], methodId: string) {
  return successfulRunsNewestFirst(runs).find((r) => r.method_id === methodId) || null;
}

function isInfrastructureFailure(run: any) {
  const error = String(run?.error || "");
  return /DaytonaValidationError/i.test(error) && /disk limit exceeded|concurrency limits/i.test(error);
}

const DEMO_PAPER_IDS = new Set(["adam2014", "wilson2017", "adamw2017"]);

function persistedWorkspaceSnapshot(papers: any[], runs: any[], state: any) {
  const refs = Array.isArray(state?.active_papers?.items)
    ? state.active_papers.items
    : [];
  const paperDigests = new Map<string, string>();
  for (const ref of refs) {
    const id = String(ref?.id || "");
    const digest = String(ref?.content_digest || "");
    if (id && /^[0-9a-f]{64}$/.test(digest)) paperDigests.set(id, digest);
  }
  const visiblePapers = papers.filter((row: any) => {
    const id = String(row.extraction?.paper?.id || row.id || "");
    return row.active === true && paperDigests.get(id) === String(row.content_digest || "");
  });

  const activeRunIds = new Set<string>(
    (Array.isArray(state?.active_run_ids?.items) ? state.active_run_ids.items : [])
      .map((id: any) => String(id || ""))
      .filter(Boolean),
  );
  const visibleRuns = runs.filter(
    (run: any) => run.active === true && activeRunIds.has(String(run.id || "")),
  );
  return {
    papers: visiblePapers,
    runs: visibleRuns,
    revision: Number.isInteger(state?.revision) ? state.revision : null,
  };
}

function workspaceView(papers: any[], runs: any[], view: string, removedRaw: string) {
  const removed = new Set(
    removedRaw
      .split(",")
      .map((id) => id.trim())
      .filter((id) => /^[a-z0-9-]{1,96}$/.test(id))
      .slice(0, 50),
  );
  let visiblePapers = papers.filter((row: any) => {
    const id = row.extraction?.paper?.id || row.id;
    return !removed.has(String(id));
  });
  if (view === "empty") visiblePapers = [];
  if (view === "demo") {
    visiblePapers = visiblePapers.filter((row: any) =>
      DEMO_PAPER_IDS.has(String(row.extraction?.paper?.id || row.id)),
    );
  }

  const methodIds = new Set<string>();
  for (const row of visiblePapers) {
    for (const method of row.extraction?.methods || []) {
      if (method.id) methodIds.add(String(method.id));
    }
  }

  let visibleRuns = runs.filter((run: any) => methodIds.has(String(run.method_id || "")));
  if (view === "empty" || view === "no-runs" || view === "demo") visibleRuns = [];
  if (view.startsWith("run:")) {
    const selected = view.slice(4);
    visibleRuns = visibleRuns.filter((run: any) => String(run.id) === selected);
  }
  return { papers: visiblePapers, runs: visibleRuns, methodIds };
}

function buildEvidence(papers: any[], runs: any[]) {
  const runByClaim: Record<string, any> = {};
  for (const r of successfulRunsNewestFirst(runs)) {
    const checks = r.claim_checks?.items || r.claim_checks || [];
    for (const chk of checks) {
      if (chk.claim_id && !runByClaim[chk.claim_id]) {
        runByClaim[chk.claim_id] = { run: r, verdict: chk.verdict, detail: chk.detail };
      }
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
        run_id: hit?.run?.id || null,
        verdict: hit?.verdict || null,
        implementation_source: hit ? implementationSource(hit.run) : null,
        provisional: hit ? isProvisionalRun(hit.run) : false,
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
  evidence?: {
    verdict: string;
    runId: string;
    detail?: string;
    implementationSource: "curated" | "llm" | "unknown";
    provisional: boolean;
  };
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
  const runByClaim: Record<string, NonNullable<ClaimRow["evidence"]>> = {};
  const successfulRuns = successfulRunsNewestFirst(runs);
  const methodsWithRuns = new Set(successfulRuns.map((r) => r.method_id).filter(Boolean));

  for (const r of successfulRuns) {
    const checks = r.claim_checks?.items || r.claim_checks || [];
    for (const chk of checks) {
      if (chk.claim_id && !runByClaim[chk.claim_id]) {
        runByClaim[chk.claim_id] = {
          verdict: chk.verdict,
          runId: r.id,
          detail: chk.detail,
          implementationSource: implementationSource(r),
          provisional: isProvisionalRun(r),
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
    runs: successfulRuns,
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

function answerAsk(
  question: string,
  ctx: WorkspaceCtx,
  cogneeConfigured = false,
  cogneeSession = "",
) {
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
      const provenance = ev.provisional
        ? ` **PROVISIONAL** (${ev.implementationSource})`
        : ` (${ev.implementationSource})`;
      return `• **${c.id}** (${c.paperId}): ${ev.verdict}${provenance} via \`${ev.runId}\`${detail}`;
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
    const wilsonEvidence = evidenced.find((c) => c.paperId === "wilson2017")?.evidence;
    const wilsonSummary = wilsonEvidence
      ? `Latest Wilson evidence: **${wilsonEvidence.verdict}**${wilsonEvidence.provisional ? " (**PROVISIONAL**)" : ""} via \`${wilsonEvidence.runId}\`.`
      : "Wilson's separable counterexample method (**wilson2017-m1**) can adjudicate the generalization conflict, but this view has no successful evidence for it.";
    return (
      "They do **not** fully agree — the graph records cross-paper conflicts:\n\n" +
      lines.join("\n\n") +
      (ctx.conflicts.length > sample.length ? `\n\n…plus ${ctx.conflicts.length - sample.length} more CONTRADICTS edge(s).` : "") +
      `\n\n${wilsonSummary}`
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
    if (!cogneeConfigured) {
      return (
        "Cognee is **not configured on this deployment**. Set `COGNEE_ENABLED=true`, " +
        "`COGNEE_SERVICE_URL`, and `COGNEE_API_KEY` to enable recall and session logging.\n\n" +
        `Graph snapshot: ${ctx.papers.length} papers, ${evidenced.length} claims with evidence, ` +
        `${ctx.runs.length} persisted runs.`
      );
    }
    return (
      "Cognee is **configured on this cloud deploy**. Successful recall and logging requests use " +
      `session \`${cogneeSession}\`; check the Cognee dashboard for delivery status.\n\n` +
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
  const runIds = [...new Set(evidenced.map((c) => c.evidence?.runId).filter(Boolean))];
  const selectedRuns = successfulRunsNewestFirst(
    ctx.runs.filter((run) => runIds.includes(run.id)),
  );
  const headlineRun = selectedRuns[0];
  let headline = `${validated.length} validated, ${refuted.length} refuted, ${untested.length} untested`;
  if (headlineRun) {
    const checks = headlineRun.claim_checks?.items || headlineRun.claim_checks || [];
    const verdicts = [...new Set(checks.map((check: any) => String(check.verdict || "")).filter(Boolean))];
    const metrics = Object.entries(headlineRun.metrics || {})
      .filter(([, value]) => typeof value === "number" && Number.isFinite(value))
      .slice(0, 3)
      .map(([key, value]) => `${key}=${Number(value).toPrecision(6).replace(/\.?0+$/, "")}`);
    headline = `${headlineRun.method_id}: ${verdicts.join("/") || "evidence recorded"}`;
    if (metrics.length) headline += ` — ${metrics.join(", ")}`;
    if (isProvisionalRun(headlineRun)) headline = `PROVISIONAL — ${headline}`;
  }

  const lines: string[] = [`**${headline}**`, ""];
  if (validated.length) {
    lines.push("**Validated**");
    validated.forEach((c) => lines.push(
      `• ${c.id}${c.evidence?.provisional ? " [PROVISIONAL]" : ""}: ${c.evidence?.detail || c.text}`,
    ));
    lines.push("");
  }
  if (refuted.length) {
    lines.push("**Refuted**");
    refuted.forEach((c) => lines.push(
      `• ${c.id}${c.evidence?.provisional ? " [PROVISIONAL]" : ""}: ${c.evidence?.detail || c.text}`,
    ));
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
      provisional_run_ids: selectedRuns.filter(isProvisionalRun).map((run) => run.id),
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
    const provenance = isProvisionalRun(run)
      ? `PROVISIONAL ${implementationSource(run)}`
      : implementationSource(run);
    steps.push(`[executor] ✓ ${run.id} [${run.backend || "daytona"}; ${provenance}] replay — ${verdicts || "metrics recorded"}`);
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
  const maxBytes = 1_000_000;
  const declaredHeader = req.headers.get("content-length");
  if (declaredHeader && !/^\d+$/.test(declaredHeader)) {
    throw new HttpError(400, "Invalid Content-Length header.");
  }
  const declared = Number(declaredHeader || 0);
  if (declared > maxBytes) throw new HttpError(413, "Request body exceeds 1 MB.");

  const reader = req.body?.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  if (reader) {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value) continue;
      received += value.byteLength;
      if (received > maxBytes) {
        await reader.cancel("request body limit exceeded").catch(() => undefined);
        throw new HttpError(413, "Request body exceeds 1 MB.");
      }
      chunks.push(value);
    }
  }
  const rawBytes = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    rawBytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  const raw = new TextDecoder().decode(rawBytes);
  try {
    return raw ? JSON.parse(raw) : {};
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(400, "Request body must be valid JSON.");
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

function publicClientIp(_req: Request, ctx: any) {
  // Headers on a public function are caller-controlled. Only runtime context
  // is an identity boundary suitable for rate limiting and visitor metadata.
  const candidates = [ctx?.request?.ip, ctx?.client?.ip];
  return candidates.map((value) => String(value || "").trim()).find(isPublicIp) || "";
}

async function enforceEdgeRateLimit(
  req: Request,
  ctx: any,
  bucket: string,
  limit: number,
): Promise<Response | null> {
  const url = new URL(req.url);
  const visitor = url.searchParams.get("visitor") || "";
  let peer = publicClientIp(req, ctx);
  if (!peer && UUID_PATTERN.test(visitor)) {
    const known = await ctx.db.query(
      "SELECT id FROM demo_visitors WHERE id = $1 LIMIT 1",
      [visitor],
    );
    if (known.rows?.length) peer = `visitor:${visitor}`;
  }
  peer ||= "anonymous";
  const key = `${bucket}:${peer}`.slice(0, 240);
  const result = await ctx.db.query(
    `WITH expired AS (
       DELETE FROM api_rate_limits
       WHERE window_start < now() - interval '10 minutes'
       RETURNING id
     )
     INSERT INTO api_rate_limits (id, window_start, request_count)
     VALUES ($1, now(), 1)
     ON CONFLICT (id) DO UPDATE SET
       request_count = CASE
         WHEN api_rate_limits.window_start < now() - interval '1 minute' THEN 1
         ELSE api_rate_limits.request_count + 1
       END,
       window_start = CASE
         WHEN api_rate_limits.window_start < now() - interval '1 minute' THEN now()
         ELSE api_rate_limits.window_start
       END
     RETURNING request_count`,
    [key],
  );
  const count = Number(result.rows?.[0]?.request_count || 0);
  return count > limit
    ? new Response(JSON.stringify({ detail: "Rate limit exceeded." }), {
        status: 429,
        headers: { ...CORS, "Content-Type": "application/json", "Retry-After": "60" },
      })
    : null;
}

async function visitorLocation(req: Request, ctx: any) {
  const ip = publicClientIp(req, ctx);
  const country = String(ctx?.request?.country || "").toUpperCase();
  const location = {
    ip,
    country: /^[A-Z]{2}$/.test(country) ? country : "",
    region: String(ctx?.request?.region || "").slice(0, 120),
    city: String(ctx?.request?.city || "").slice(0, 120),
  };
  return location;
}

function validEmail(value: string) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value) && value.length <= 254;
}

async function registerVisitor(req: Request, ctx: any, body: any) {
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
  if (!UUID_PATTERN.test(visitorId)) return;
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

export {
  buildEvidence,
  buildGraph,
  latestRunForMethod,
  persistedWorkspaceSnapshot,
  publicClientIp,
  readJsonBody,
  runRowToRecord,
  workspaceView,
};

export default async function handler(req: Request, ctx: any): Promise<Response> {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });

  const url = new URL(req.url);
  const route = url.searchParams.get("route") || "health";
  const visitorId = url.searchParams.get("visitor") || "";
  const requestedView = url.searchParams.get("view") || "full";
  const view = /^(?:full|empty|no-runs|demo|run:[A-Za-z0-9._:-]{1,160})$/.test(requestedView)
    ? requestedView
    : "full";
  const removed = url.searchParams.get("removed") || "";

  try {
    const requestBody = ["POST", "PUT", "PATCH", "DELETE"].includes(req.method)
      ? await readJsonBody(req)
      : {};
    const defaultLimited = await enforceEdgeRateLimit(req, ctx, "default", 120);
    if (defaultLimited) return defaultLimited;
    if (req.method === "POST" && route === "register") {
      const limited = await enforceEdgeRateLimit(req, ctx, "registration", 10);
      return limited || await registerVisitor(req, ctx, requestBody);
    }
    if (req.method === "GET" && route === "admin/users") return await adminUsers(req, ctx);
    if (req.method === "POST") {
      const bucket = route.startsWith("run/") ? "execution" : "agent";
      const limit = bucket === "execution" ? 10 : 30;
      const limited = await enforceEdgeRateLimit(req, ctx, bucket, limit);
      if (limited) return limited;
    }

    const [papersRes, runsRes, stateRes] = await Promise.all([
      ctx.db.query("SELECT * FROM papers WHERE active = true ORDER BY year, id"),
      ctx.db.query("SELECT * FROM runs WHERE active = true ORDER BY id"),
      ctx.db.query("SELECT * FROM workspace_state WHERE id = $1 LIMIT 1", ["default"]),
    ]);
    const allPapers = papersRes.rows || [];
    const allRuns = runsRes.rows || [];
    const persisted = persistedWorkspaceSnapshot(
      allPapers,
      allRuns,
      stateRes.rows?.[0] || null,
    );
    const storedPapers = persisted.papers;
    // Quota failures are operational audit records, not scientific evidence.
    // Keep them in Butterbase but do not render or replay them as graph runs.
    const storedRuns = persisted.runs.filter((run: any) => !isInfrastructureFailure(run));
    const scoped = workspaceView(storedPapers, storedRuns, view, removed);
    const papers = scoped.papers;
    const runs = scoped.runs;
    const ws = buildWorkspaceCtx(papers, runs);

    if (route === "health") {
      return json({
        ok: true,
        papers: storedPapers.length,
        runs: storedRuns.length,
        workspace_revision: persisted.revision,
        stored_papers: allPapers.length,
        stored_runs: allRuns.length,
      });
    }

    if (route === "graph") return json(buildGraph(papers, runs));

    if (route === "evidence") return json(buildEvidence(papers, runs));

    if (route === "workspace") {
      const ids = papers.map((p: any) => (p.extraction?.paper?.id || p.id));
      return json({
        empty: papers.length === 0,
        papers: papers.length,
        claims: buildEvidence(papers, runs).length,
        runs: runs.length,
        revision: persisted.revision,
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
        const demo = workspaceView(storedPapers, storedRuns, "demo", "");
        return json({
          papers: demo.papers.length,
          claims: buildEvidence(demo.papers, []).length,
          empty: false,
          replay: true,
          view: "demo",
          message: `Demo loaded — ${demo.papers.length} papers (Adam · Wilson · AdamW), no runs yet.`,
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
        if (!scoped.methodIds.has(methodId)) {
          return json({ detail: `Method ${methodId} is not active in this workspace view.` }, 404);
        }
        const hit = latestRunForMethod(storedRuns, methodId);
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
        const question = String(requestBody.question || "").trim();
        if (!question) return json({ detail: "question is required" }, 400);
        let answer = answerAsk(
          question,
          ws,
          cogneeReady(ctx),
          cogneeSessionId(ctx, visitorId),
        );
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
        const r = answerConduct(ws);
        const msg = String(requestBody.message || "Full evidence workflow");
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
    if (e instanceof HttpError) return json({ detail: e.message }, e.status);
    console.error("verigraph edge request failed", e);
    return json({ error: "Internal server error." }, 500);
  }
}
