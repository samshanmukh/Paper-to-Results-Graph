/** Browser-local paper vault for the Butterbase cloud demo.
 *  New papers are extracted (small requests) and stored in localStorage,
 *  then merged into the graph/evidence views without writing shared Butterbase rows.
 */
(function () {
  const STORAGE_KEY = "vg_local_papers_v1";
  const MAX_PAPERS = 12;
  const MAX_TEXT_CHARS = 20000;

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function loadVault() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      const list = raw ? JSON.parse(raw) : [];
      return Array.isArray(list) ? list : [];
    } catch {
      return [];
    }
  }

  function saveVault(list) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list.slice(0, MAX_PAPERS)));
  }

  function upsertLocalPaper(extraction, meta = {}) {
    const paper = extraction?.paper;
    if (!paper?.id) throw new Error("extraction missing paper.id");
    const list = loadVault().filter((p) => p.id !== paper.id);
    list.unshift({
      id: paper.id,
      title: paper.title,
      year: paper.year,
      arxiv: paper.arxiv || null,
      topic: paper.topic || null,
      extraction,
      source: meta.source || "local",
      added_at: new Date().toISOString(),
      local: true,
    });
    saveVault(list);
    return list[0];
  }

  function removeLocalPaper(paperId) {
    saveVault(loadVault().filter((p) => p.id !== paperId));
  }

  function clearLocalPapers() {
    localStorage.removeItem(STORAGE_KEY);
  }

  function vaultAsButterbaseRows() {
    return loadVault().map((p) => ({
      id: p.id,
      title: p.title,
      year: p.year,
      arxiv: p.arxiv,
      topic: p.topic,
      extraction: p.extraction,
      local: true,
    }));
  }

  /** Merge local papers into a graph payload from /api/graph. */
  function mergeGraph(g) {
    const locals = vaultAsButterbaseRows();
    if (!locals.length) return g;
    const built = buildLocalGraph(locals);
    const seen = new Set((g.nodes || []).map((n) => n.eid));
    const nodes = [...(g.nodes || [])];
    for (const n of built.nodes) {
      if (!seen.has(n.eid)) {
        nodes.push(n);
        seen.add(n.eid);
      }
    }
    const edgeKey = (e) => `${e.src}|${e.rel}|${e.dst}`;
    const edgesSeen = new Set((g.edges || []).map(edgeKey));
    const edges = [...(g.edges || [])];
    for (const e of built.edges) {
      const k = edgeKey(e);
      if (!edgesSeen.has(k)) {
        edges.push(e);
        edgesSeen.add(k);
      }
    }
    return { nodes, edges };
  }

  function buildLocalGraph(papers) {
    const nodes = [];
    const edges = [];
    const claimIndex = {};
    for (const row of papers) {
      const data = row.extraction || {};
      const p = data.paper || row;
      const pid = p.id || row.id;
      nodes.push({
        eid: `p-${pid}`,
        label: "Paper",
        key: pid,
        caption: p.title,
        props: {
          id: pid,
          title: p.title,
          year: p.year,
          arxiv: p.arxiv,
          topic: p.topic,
          local: true,
        },
      });
      for (const c of data.claims || []) {
        const cid = `c-${c.id}`;
        claimIndex[c.id] = cid;
        nodes.push({
          eid: cid,
          label: "Claim",
          key: c.id,
          caption: c.text,
          props: { id: c.id, text: c.text, metric: c.metric, local: true },
        });
        edges.push({ src: cid, dst: `p-${pid}`, rel: "FROM" });
      }
      for (const m of data.methods || []) {
        nodes.push({
          eid: `m-${m.id}`,
          label: "Method",
          key: m.id,
          caption: m.name,
          props: {
            id: m.id,
            name: m.name,
            description: m.description,
            runnable_hint: m.runnable_hint,
            params: JSON.stringify(m.params || []),
            local: true,
          },
        });
        edges.push({ src: `m-${m.id}`, dst: `p-${pid}`, rel: "DESCRIBED_IN" });
      }
      for (const cited of data.cites || []) {
        edges.push({ src: `p-${pid}`, dst: `p-${cited}`, rel: "CITES" });
      }
      for (const rel of data.claim_relations || []) {
        const from = claimIndex[rel.from];
        const to = claimIndex[rel.to] || `c-${rel.to}`;
        if (from) edges.push({ src: from, dst: to, rel: rel.type });
      }
    }
    return { nodes, edges };
  }

  function mergeEvidence(rows) {
    const out = Array.isArray(rows) ? [...rows] : [];
    const have = new Set(out.map((r) => r.claim));
    for (const p of loadVault()) {
      const data = p.extraction || {};
      const pid = data.paper?.id || p.id;
      for (const c of data.claims || []) {
        if (have.has(c.id)) continue;
        out.push({
          paper: pid,
          claim: c.id,
          text: c.text,
          evidence: "no runs yet",
          local: true,
        });
        have.add(c.id);
      }
    }
    return out;
  }

  function mergeWorkspace(ws) {
    const locals = loadVault();
    if (!locals.length) return ws;
    const ids = new Set([...(ws.paper_ids || []), ...locals.map((p) => p.id)]);
    const detail = [...(ws.papers_detail || [])];
    const have = new Set(detail.map((d) => d.id));
    for (const p of locals) {
      if (have.has(p.id)) continue;
      const data = p.extraction || {};
      detail.push({
        id: p.id,
        title: p.title,
        year: p.year,
        arxiv: p.arxiv,
        claims: (data.claims || []).length,
        methods: (data.methods || []).length,
        local: true,
      });
    }
    return {
      ...ws,
      empty: ids.size === 0,
      papers: ids.size,
      paper_ids: [...ids],
      papers_detail: detail,
      local_papers: locals.length,
    };
  }

  function heuristicExtractFromText(text, hint = {}) {
    const cleaned = String(text || "").replace(/\s+/g, " ").trim();
    if (cleaned.length < 80) throw new Error("paper text too short to extract");
    const lines = String(text || "")
      .split(/\n+/)
      .map((l) => l.trim())
      .filter(Boolean);
    const title =
      hint.title ||
      lines.find((l) => l.length > 12 && l.length < 180 && !/^arXiv:/i.test(l)) ||
      "Untitled local paper";
    let id = hint.id;
    if (!id) {
      const year = (hint.year && String(hint.year)) || (title.match(/\b(19|20)\d{2}\b/) || [])[0] || "2024";
      const slug = title
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "")
        .slice(0, 18) || "localpaper";
      id = `${slug}${year}`.slice(0, 32);
    }
    const sentences = cleaned
      .split(/(?<=[.!?])\s+/)
      .map((s) => s.trim())
      .filter((s) => s.length > 40 && s.length < 320)
      .slice(0, 8);
    const claims = sentences.slice(0, 3).map((s, i) => ({
      id: `${id}-c${i + 1}`,
      text: s,
      metric: null,
    }));
    if (!claims.length) {
      claims.push({
        id: `${id}-c1`,
        text: cleaned.slice(0, 200),
        metric: null,
      });
    }
    return {
      paper: {
        id,
        title: title.slice(0, 240),
        authors: hint.authors || ["local"],
        year: Number(hint.year) || Number((title.match(/\b(19|20)\d{2}\b/) || [])[0]) || new Date().getFullYear(),
        arxiv: hint.arxiv || null,
        topic: hint.topic || "local-import",
      },
      claims,
      methods: [
        {
          id: `${id}-m1`,
          name: "Toy reproduction experiment",
          description: `Simplified numpy simulation of the core claim in “${title.slice(0, 80)}”.`,
          runnable_hint:
            "Build a tiny synthetic task; sample candidates; score with a verifier; report accuracy_at_n vs n_candidates.",
          params: [
            { name: "n_candidates", default: 8, description: "best-of-n width" },
            { name: "n_trials", default: 200, description: "synthetic problems" },
            { name: "noise", default: 0.3, description: "task difficulty" },
          ],
        },
      ],
      datasets: [],
      cites: [],
      claim_relations: [],
    };
  }

  async function extractArxiv(url) {
    const res = await fetch("/api/extract-local-arxiv", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.error?.message || `HTTP ${res.status}`);
    return data;
  }

  async function extractTextRemote(text, hint = {}) {
    const payload = {
      text: String(text).slice(0, MAX_TEXT_CHARS),
      title: hint.title || "",
      arxiv: hint.arxiv || "",
    };
    const res = await fetch("/api/extract-local-text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.error?.message || `HTTP ${res.status}`);
    return data;
  }

  async function loadPdfJs() {
    if (window.pdfjsLib) return window.pdfjsLib;
    await new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js";
      s.onload = resolve;
      s.onerror = () => reject(new Error("failed to load pdf.js"));
      document.head.appendChild(s);
    });
    const lib = window.pdfjsLib;
    lib.GlobalWorkerOptions.workerSrc =
      "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
    return lib;
  }

  async function pdfToText(file) {
    const lib = await loadPdfJs();
    const buf = await file.arrayBuffer();
    const pdf = await lib.getDocument({ data: buf }).promise;
    const maxPages = Math.min(pdf.numPages, 12);
    const parts = [];
    for (let i = 1; i <= maxPages; i++) {
      const page = await pdf.getPage(i);
      const content = await page.getTextContent();
      parts.push(content.items.map((it) => it.str).join(" "));
    }
    return parts.join("\n").trim();
  }

  async function importArxiv(url) {
    const result = await extractArxiv(url);
    const extraction = result.extraction || result;
    return upsertLocalPaper(extraction, { source: result.source || "arxiv" });
  }

  async function importFile(file) {
    let text = "";
    const name = file.name || "upload";
    if (/\.pdf$/i.test(name) || file.type === "application/pdf") {
      text = await pdfToText(file);
    } else {
      text = await file.text();
    }
    if (text.length < 200) throw new Error("could not read enough text from that file");
    // Prefer remote structure if available; fall back to pure client heuristic
    try {
      const result = await extractTextRemote(text, { title: name.replace(/\.[^.]+$/, "") });
      return upsertLocalPaper(result.extraction || result, { source: result.source || "file" });
    } catch (e) {
      const extraction = heuristicExtractFromText(text, { title: name.replace(/\.[^.]+$/, "") });
      return upsertLocalPaper(extraction, { source: "file-local" });
    }
  }

  function renderLocalPapersList(container) {
    if (!container) return;
    const list = loadVault();
    if (!list.length) {
      container.innerHTML = "";
      container.hidden = true;
      return;
    }
    container.hidden = false;
    container.innerHTML =
      `<h3>Local papers <span class="papers-sub">(${list.length} in this browser)</span></h3>` +
      list
        .map(
          (p) => `<div class="paper-row local-paper" data-local-id="${esc(p.id)}">
          <div class="paper-row-main">
            <div class="paper-row-id">${esc(p.id)} · local</div>
            <div class="paper-row-title">${esc(p.title)}</div>
            <div class="paper-row-meta">${esc(p.source || "local")} · ${(p.extraction?.claims || []).length} claims · ${(p.extraction?.methods || []).length} methods</div>
          </div>
          <button type="button" class="btn-remove" data-del-local="${esc(p.id)}" title="Remove local paper">✕</button>
        </div>`
        )
        .join("");
    container.querySelectorAll("[data-del-local]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        removeLocalPaper(btn.dataset.delLocal);
        if (typeof loadGraph === "function") loadGraph();
        if (typeof loadEvidence === "function") loadEvidence();
        if (typeof refreshWorkspaceMeta === "function") refreshWorkspaceMeta();
        renderLocalPapersList(container);
        if (typeof showDemoToast === "function") showDemoToast(`Removed local paper ${btn.dataset.delLocal}`);
      });
    });
  }

  window.verigraphLocalPapers = {
    loadVault,
    saveVault,
    upsertLocalPaper,
    removeLocalPaper,
    clearLocalPapers,
    mergeGraph,
    mergeEvidence,
    mergeWorkspace,
    importArxiv,
    importFile,
    heuristicExtractFromText,
    renderLocalPapersList,
    STORAGE_KEY,
  };
})();
