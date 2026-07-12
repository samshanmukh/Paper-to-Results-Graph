/** Advanced Verigraph features: compare, batch, brief, timeline, citations, workspaces. */
(function () {
  const VISITOR_ID_KEY = "vg_visitor_id";
  const VISITOR_EMAIL_KEY = "vg_visitor_email";
  const DISPLAY_NAME_KEY = "vg_display_name";
  let layoutMode = "flow"; // flow | citations
  let compareRunsCache = [];

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function toast(msg, ms = 3200) {
    if (typeof showDemoToast === "function") showDemoToast(msg, ms);
  }

  async function downloadEvidenceBrief() {
    try {
      const res = await fetch("/api/evidence-brief");
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      const text = await res.text();
      const blob = new Blob([text], { type: "text/markdown" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const stamp = new Date().toISOString().replace(/[:.]/g, "").slice(0, 15) + "Z";
      a.href = url;
      a.download = `verigraph-brief-${stamp}.md`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      // Print-friendly HTML version in a new window
      const w = window.open("", "_blank");
      if (w) {
        w.document.write(
          `<!doctype html><html><head><title>Verigraph evidence brief</title>
          <style>body{font-family:IBM Plex Sans,system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 20px;line-height:1.55;color:#171A20}
          pre{white-space:pre-wrap;font-family:IBM Plex Mono,monospace;font-size:13px;background:#F7F7F4;padding:16px;border-radius:10px}
          @media print{button{display:none}}</style></head><body>
          <button onclick="print()">Print / Save as PDF</button>
          <pre>${esc(text)}</pre></body></html>`
        );
        w.document.close();
      }
      toast("Evidence brief ready");
    } catch (e) {
      toast("Brief failed: " + (e.message || e));
    }
  }

  async function runBatch() {
    const btn = document.getElementById("batch-run-btn");
    if (!btn || btn.disabled) return;
    btn.disabled = true;
    const prev = btn.textContent;
    btn.textContent = "Batch running…";
    try {
      const plan = await (await fetch("/api/batch-plan")).json();
      const pending = plan.pending || [];
      if (!pending.length) {
        toast("All methods already have runs");
        return;
      }
      if (!confirm(`Run ${pending.length} never-run method(s)?\n${pending.join(", ")}`)) return;
      toast(`Batch: ${pending.length} methods…`, 5000);
      const res = await fetch("/api/batch-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ method_ids: pending }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      const ok = (data.ran || []).filter((r) => !r.error).length;
      toast(`Batch done — ${ok}/${data.count} succeeded`);
      if (typeof loadGraph === "function") await loadGraph(true);
      if (typeof loadEvidence === "function") await loadEvidence();
      if (typeof loadInsights === "function") await loadInsights();
      await refreshAdvancedPanels();
    } catch (e) {
      toast("Batch failed: " + (e.message || e));
    } finally {
      btn.disabled = false;
      btn.textContent = prev;
    }
  }

  async function loadCompareOptions() {
    const a = document.getElementById("compare-a");
    const b = document.getElementById("compare-b");
    if (!a || !b) return;
    const runs = await (await fetch("/api/runs?limit=40")).json();
    compareRunsCache = Array.isArray(runs) ? runs : [];
    const opts = compareRunsCache
      .map(
        (r) =>
          `<option value="${esc(r.run_id)}">${esc(r.run_id)} · ${esc(r.method_id)}</option>`
      )
      .join("");
    a.innerHTML = `<option value="">Run A…</option>` + opts;
    b.innerHTML = `<option value="">Run B…</option>` + opts;
    if (compareRunsCache.length >= 2) {
      a.value = compareRunsCache[1].run_id;
      b.value = compareRunsCache[0].run_id;
    }
  }

  async function runCompare() {
    const runA = document.getElementById("compare-a")?.value;
    const runB = document.getElementById("compare-b")?.value;
    const out = document.getElementById("compare-out");
    if (!runA || !runB || !out) return;
    out.innerHTML = `<div class="evidence-empty">Comparing…</div>`;
    try {
      const res = await fetch("/api/compare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_a: runA, run_b: runB }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      const s = data.summary || {};
      out.innerHTML = `
        <div class="compare-summary">${esc(data.method_id || "")} ·
          ${s.params_changed || 0} paramΔ · ${s.metrics_changed || 0} metricΔ · ${s.verdicts_flipped || 0} flips
          ${data.same_method ? "" : " · <em>different methods</em>"}</div>
        <div class="compare-grid">
          <div><div class="k">Params</div>${
            (data.param_diff || []).length
              ? data.param_diff
                  .map((p) => `<div class="diff-row"><b>${esc(p.key)}</b><span>${esc(p.a)} → ${esc(p.b)}</span></div>`)
                  .join("")
              : "<div class='muted'>identical</div>"
          }</div>
          <div><div class="k">Metrics</div>${
            (data.metric_diff || [])
              .filter((m) => m.changed)
              .map(
                (m) =>
                  `<div class="diff-row"><b>${esc(m.key)}</b><span>${esc(m.a)} → ${esc(m.b)}${
                    m.delta != null ? ` (Δ ${Number(m.delta).toFixed(4)})` : ""
                  }</span></div>`
              )
              .join("") || "<div class='muted'>identical</div>"
          }</div>
        </div>
        <div class="k" style="margin-top:10px">Verdicts</div>
        ${(data.claim_diff || [])
          .map(
            (c) =>
              `<div class="diff-row ${c.flipped ? "flip" : ""}"><b>${esc(c.claim_id)}</b><span>${esc(
                c.a || "—"
              )} → ${esc(c.b || "—")}</span></div>`
          )
          .join("") || "<div class='muted'>no claim checks</div>"}`;
    } catch (e) {
      out.innerHTML = `<div class="evidence-empty">${esc(e.message || e)}</div>`;
    }
  }

  async function loadTimeline() {
    const el = document.getElementById("timeline-list");
    if (!el) return;
    try {
      const rows = await (await fetch("/api/timeline")).json();
      if (!Array.isArray(rows) || !rows.length) {
        el.innerHTML = `<div class="evidence-empty">No claim timeline yet — run methods to create verdict history.</div>`;
        return;
      }
      el.innerHTML = rows
        .map((row) => {
          const events = (row.events || [])
            .map(
              (e) =>
                `<div class="tl-event ${e.flipped ? "flip" : ""}">
                  <span class="tl-ver">${esc(e.verdict)}</span>
                  <span class="tl-meta">${esc(e.run_id)} · ${esc(e.method_id || "")}${
                  e.flipped ? " · flipped from " + esc(e.previous) : ""
                }</span>
                  ${e.detail ? `<div class="tl-detail">${esc(e.detail)}</div>` : ""}
                </div>`
            )
            .join("");
          return `<div class="tl-claim"><div class="tl-head"><span class="ev-id">${esc(
            row.claim_id
          )}</span><span class="conflict-status">${esc(row.latest || "—")} · ${
            row.flips || 0
          } flips</span></div>${events}</div>`;
        })
        .join("");
    } catch (e) {
      el.innerHTML = `<div class="evidence-empty">${esc(e.message || e)}</div>`;
    }
  }

  function setLayoutMode(mode) {
    layoutMode = mode === "citations" ? "citations" : "flow";
    document.querySelectorAll(".layout-mode-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.mode === layoutMode);
    });
    applyLayoutFilter();
    toast(layoutMode === "citations" ? "Citation network view" : "Evidence-flow view");
  }

  function applyLayoutFilter() {
    if (typeof nodesDS === "undefined" || !nodesDS || typeof edgesDS === "undefined" || !edgesDS) return;
    if (layoutMode === "flow") {
      // restore opacities
      nodesDS.get().forEach((n) => nodesDS.update({ id: n.id, opacity: 1, hidden: false }));
      edgesDS.get().forEach((e) => edgesDS.update({ id: e.id, hidden: false, opacity: 1 }));
      if (typeof network !== "undefined" && network) network.fit({ animation: { duration: 400 } });
      return;
    }
    // citations: emphasize Paper nodes + CITES edges
    const paperIds = new Set();
    nodesDS.get().forEach((n) => {
      const isPaper = n._raw?.label === "Paper";
      if (isPaper) paperIds.add(n.id);
      nodesDS.update({
        id: n.id,
        opacity: isPaper ? 1 : 0.12,
        hidden: false,
      });
    });
    edgesDS.get().forEach((e) => {
      const isCites = e._rel === "CITES" || e.label === "CITES" || e.dashes === true && false;
      const rel = e._rel || e.label || "";
      const keep = rel === "CITES";
      edgesDS.update({
        id: e.id,
        hidden: !keep,
        opacity: keep ? 1 : 0.05,
        width: keep ? 2.5 : 1,
      });
    });
    // Also keep paper nodes that have CITES
    if (typeof network !== "undefined" && network) {
      network.fit({ nodes: [...paperIds], animation: { duration: 500 } });
    }
  }

  // Tag edges with relation type when graph builds — hook after animateGraph if needed
  const origEdgeStyle = window.edgeStyle;
  if (typeof edgeStyle === "function") {
    // wrap later via monkeypatch after definitions
  }

  async function refreshWorkspaces() {
    const list = document.getElementById("workspace-saves");
    if (!list) return;
    const visitor = localStorage.getItem(VISITOR_ID_KEY) || "";
    const email = localStorage.getItem(VISITOR_EMAIL_KEY) || "";
    const q = visitor
      ? `visitor=${encodeURIComponent(visitor)}`
      : email
        ? `email=${encodeURIComponent(email)}`
        : "";
    if (!q) {
      list.innerHTML = `<div class="evidence-empty">Sign in with email to save named workspaces.</div>`;
      return;
    }
    try {
      const data = await (await fetch(`/api/saved-workspaces?${q}`)).json();
      const rows = data.workspaces || [];
      if (!rows.length) {
        list.innerHTML = `<div class="evidence-empty">No saved workspaces yet.</div>`;
        return;
      }
      list.innerHTML = rows
        .map(
          (w) => `<div class="ws-row" data-id="${esc(w.id)}">
          <div class="ws-main"><div class="ws-name">${esc(w.name)}</div>
          <div class="ws-meta">${esc((w.updated_at || w.created_at || "").toString().slice(0, 19))}</div></div>
          <button type="button" class="btn-ghost ws-load">Load</button>
          <button type="button" class="btn-remove ws-del" title="Delete">✕</button>
        </div>`
        )
        .join("");
      list.querySelectorAll(".ws-row").forEach((row) => {
        const id = row.dataset.id;
        const snap = rows.find((r) => r.id === id)?.snapshot || {};
        row.querySelector(".ws-load")?.addEventListener("click", () => loadWorkspaceSnapshot(snap));
        row.querySelector(".ws-del")?.addEventListener("click", async () => {
          await fetch(`/api/saved-workspaces/${id}?visitor=${encodeURIComponent(visitor)}`, {
            method: "DELETE",
          });
          refreshWorkspaces();
        });
      });
    } catch (e) {
      list.innerHTML = `<div class="evidence-empty">${esc(e.message || e)}</div>`;
    }
  }

  function loadWorkspaceSnapshot(snap) {
    try {
      if (snap.removed_papers && typeof setRemovedPapers === "function") {
        setRemovedPapers(snap.removed_papers);
      }
      if (snap.view && typeof setBbView === "function") setBbView(snap.view);
      if (snap.layout_mode) setLayoutMode(snap.layout_mode);
      toast("Workspace loaded");
      if (typeof loadGraph === "function") loadGraph();
      if (typeof loadEvidence === "function") loadEvidence();
    } catch (e) {
      toast("Load failed: " + e.message);
    }
  }

  async function saveWorkspace() {
    const nameInput = document.getElementById("ws-name");
    const displayInput = document.getElementById("ws-display-name");
    const name = (nameInput?.value || "").trim();
    if (!name) {
      toast("Enter a workspace name");
      return;
    }
    const display_name = (displayInput?.value || "").trim();
    if (display_name) localStorage.setItem(DISPLAY_NAME_KEY, display_name);
    const removed = typeof getRemovedPapers === "function" ? [...getRemovedPapers()] : [];
    const snapshot = {
      removed_papers: removed,
      view: typeof getBbView === "function" ? getBbView() : "full",
      layout_mode: layoutMode,
      saved_at: new Date().toISOString(),
    };
    const res = await fetch("/api/saved-workspaces", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        snapshot,
        visitor_id: localStorage.getItem(VISITOR_ID_KEY) || "",
        email: localStorage.getItem(VISITOR_EMAIL_KEY) || "",
        display_name,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    toast("Workspace saved");
    if (nameInput) nameInput.value = "";
    refreshWorkspaces();
  }

  async function refreshAdvancedPanels() {
    await Promise.all([loadCompareOptions(), loadTimeline(), refreshWorkspaces()]);
    const planEl = document.getElementById("batch-pending");
    if (planEl) {
      try {
        const plan = await (await fetch("/api/batch-plan")).json();
        planEl.textContent = plan.count
          ? `${plan.count} never-run: ${(plan.pending || []).slice(0, 4).join(", ")}`
          : "All methods have runs";
      } catch {
        planEl.textContent = "";
      }
    }
  }

  function patchEdgeRelationTags() {
    // Ensure edges keep _rel for citation filter — patch EDGE building if present
    if (typeof edgeStyle !== "function") return;
    const prev = edgeStyle;
    window.edgeStyle = function (e) {
      const styled = prev(e);
      if (styled) styled._rel = e.rel;
      return styled;
    };
  }

  function initAdvancedFeatures() {
    patchEdgeRelationTags();
    document.getElementById("brief-btn")?.addEventListener("click", downloadEvidenceBrief);
    document.getElementById("batch-run-btn")?.addEventListener("click", runBatch);
    document.getElementById("compare-go")?.addEventListener("click", runCompare);
    document.getElementById("ws-save-btn")?.addEventListener("click", () => {
      saveWorkspace().catch((e) => toast("Save failed: " + (e.message || e)));
    });
    document.querySelectorAll(".layout-mode-btn").forEach((b) => {
      b.addEventListener("click", () => setLayoutMode(b.dataset.mode));
    });
    const display = document.getElementById("ws-display-name");
    if (display) display.value = localStorage.getItem(DISPLAY_NAME_KEY) || "";

    // Hook insights tab refresh
    const origSetTab = window.setTab;
    if (typeof origSetTab === "function") {
      window.setTab = function (tab) {
        origSetTab(tab);
        if (tab === "insights") refreshAdvancedPanels();
      };
    }

    // Live-run badge from health
    fetch("/api/health")
      .then((r) => r.json())
      .then((h) => {
        const badge = document.getElementById("live-run-badge");
        if (!badge) return;
        if (h.live_run) {
          badge.textContent = "Live Daytona";
          badge.classList.add("live");
          badge.title = "Cloud RUN executes in Daytona sandboxes";
        } else {
          badge.textContent = "Replay mode";
          badge.title = "Cloud RUN replays persisted results (set DAYTONA_API_KEY for live)";
        }
      })
      .catch(() => {});
  }

  window.verigraphAdvanced = {
    init: initAdvancedFeatures,
    refresh: refreshAdvancedPanels,
    setLayoutMode,
    downloadEvidenceBrief,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAdvancedFeatures);
  } else {
    // defer until main script defines helpers
    setTimeout(initAdvancedFeatures, 0);
  }
})();
