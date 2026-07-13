const test = require("node:test");
const assert = require("node:assert/strict");

const edgeModule = require("../.tsbuild/verigraph_api.js");
const handler = edgeModule.default;
const {
  buildEvidence,
  buildGraph,
  latestRunForMethod,
  persistedWorkspaceSnapshot,
  publicClientIp,
  readJsonBody,
  runRowToRecord,
  workspaceView,
} = edgeModule;

function paper(id, year, claims = [], methods = [], relations = []) {
  return {
    id,
    extraction: {
      paper: { id, title: id, year },
      claims,
      methods,
      claim_relations: relations,
    },
  };
}

test("buildGraph keeps forward cross-paper claim relations", () => {
  const papers = [
    paper(
      "first2020",
      2020,
      [{ id: "first2020-c1", text: "first" }],
      [],
      [{ from: "first2020-c1", to: "later2021-c1", type: "CONTRADICTS" }],
    ),
    paper("later2021", 2021, [{ id: "later2021-c1", text: "later" }]),
  ];
  const graph = buildGraph(papers, []);
  assert.ok(
    graph.edges.some(
      edge => edge.src === "c-first2020-c1" &&
        edge.dst === "c-later2021-c1" && edge.rel === "CONTRADICTS",
    ),
  );
});

test("latestRunForMethod prefers a successful persisted result", () => {
  const runs = [
    { id: "run-example-m1-20260102", method_id: "example-m1", status: "failure", exit_code: -1, error: "quota" },
    { id: "run-example-m1-20260101", method_id: "example-m1", status: "success", exit_code: 0, error: null },
  ];
  assert.equal(latestRunForMethod(runs, "example-m1").id, "run-example-m1-20260101");
  assert.equal(latestRunForMethod([runs[0]], "example-m1"), null);
});

test("run replay preserves effective params and explicit overrides", () => {
  const record = runRowToRecord({
    id: "run-example-m1-20260101",
    method_id: "example-m1",
    backend: "daytona",
    status: "success",
    exit_code: 0,
    implementation_source: "curated",
    implementation_fingerprint: "a".repeat(64),
    context_digest: "b".repeat(64),
    params: { steps: 100, learning_rate: 0.01 },
    parameter_overrides: { steps: 100 },
    metrics: { score: 0.9 },
    claim_checks: { items: [{ claim_id: "example-c1", verdict: "VALIDATES" }] },
  });

  assert.deepEqual(record.params, { steps: 100, learning_rate: 0.01 });
  assert.deepEqual(record.parameter_overrides, { steps: 100 });
  assert.deepEqual(record.result.params, record.params);
  assert.deepEqual(record.result.metrics, { score: 0.9 });
});

test("evidence selects the latest successful run and preserves provenance", () => {
  const papers = [paper("example2020", 2020, [{ id: "example2020-c1", text: "claim" }])];
  const runs = [
    {
      id: "run-old",
      method_id: "example2020-m1",
      status: "success",
      exit_code: 0,
      created_at: "2026-01-01T00:00:00Z",
      implementation_source: "curated",
      claim_checks: { items: [{ claim_id: "example2020-c1", verdict: "VALIDATES" }] },
    },
    {
      id: "run-failed",
      method_id: "example2020-m1",
      status: "failure",
      exit_code: 1,
      error: "failed",
      created_at: "2026-01-03T00:00:00Z",
      claim_checks: { items: [{ claim_id: "example2020-c1", verdict: "REFUTES" }] },
    },
    {
      id: "run-new",
      method_id: "example2020-m1",
      status: "success",
      exit_code: 0,
      created_at: "2026-01-02T00:00:00Z",
      implementation_source: "llm",
      provisional: true,
      claim_checks: { items: [{ claim_id: "example2020-c1", verdict: "REFUTES" }] },
    },
  ];
  const [row] = buildEvidence(papers, runs);
  assert.equal(row.evidence, "REFUTES by run-new");
  assert.equal(row.implementation_source, "llm");
  assert.equal(row.provisional, true);
});

test("demo and selected-run views scope both papers and evidence", () => {
  const papers = [
    paper("adam2014", 2014, [], [{ id: "adam2014-m1" }]),
    paper("wilson2017", 2017, [], [{ id: "wilson2017-m1" }]),
    paper("adamw2017", 2017, [], [{ id: "adamw2017-m1" }]),
    paper("other2020", 2020, [], [{ id: "other2020-m1" }]),
  ];
  const runs = [
    { id: "run-a", method_id: "wilson2017-m1" },
    { id: "run-b", method_id: "wilson2017-m1" },
    { id: "run-other", method_id: "other2020-m1" },
  ];
  const demo = workspaceView(papers, runs, "demo", "");
  assert.deepEqual(demo.papers.map(row => row.id).sort(), ["adam2014", "adamw2017", "wilson2017"]);
  assert.equal(demo.runs.length, 0);

  const selected = workspaceView(papers, runs, "run:run-a", "other2020");
  assert.deepEqual(selected.runs.map(run => run.id), ["run-a"]);
  assert.ok(!selected.papers.some(row => row.id === "other2020"));
});

test("unknown or unhashed provenance is always provisional", () => {
  const papers = [paper("example2020", 2020, [{ id: "example2020-c1", text: "claim" }])];
  const runs = [{
    id: "run-legacy",
    method_id: "example2020-m1",
    status: "success",
    exit_code: 0,
    implementation_source: null,
    provisional: false,
    claim_checks: { items: [{ claim_id: "example2020-c1", verdict: "VALIDATES" }] },
  }];
  const [row] = buildEvidence(papers, runs);
  assert.equal(row.implementation_source, "unknown");
  assert.equal(row.provisional, true);
});

test("persisted workspace snapshot excludes stale paper revisions and reset runs", () => {
  const current = paper("example2020", 2020, [], [{ id: "example2020-m1" }]);
  current.content_digest = "a".repeat(64);
  current.active = true;
  const replaced = paper("replaced2020", 2020, [], [{ id: "replaced2020-m1" }]);
  replaced.content_digest = "b".repeat(64);
  replaced.active = true;
  const state = {
    revision: 12,
    active_papers: {
      items: [
        { id: "example2020", content_digest: "a".repeat(64) },
        // The row has already advanced to a new revision, so it must remain
        // hidden until workspace_state is published with the same digest.
        { id: "replaced2020", content_digest: "c".repeat(64) },
      ],
    },
    active_run_ids: { items: ["run-current"] },
  };
  const snapshot = persistedWorkspaceSnapshot(
    [current, replaced],
    [
      { id: "run-current", method_id: "example2020-m1", active: true },
      { id: "run-before-reset", method_id: "example2020-m1", active: false },
    ],
    state,
  );
  assert.deepEqual(snapshot.papers.map(row => row.id), ["example2020"]);
  assert.deepEqual(snapshot.runs.map(row => row.id), ["run-current"]);
  assert.equal(snapshot.revision, 12);
});

test("missing workspace state defaults to an empty cloud view", () => {
  const snapshot = persistedWorkspaceSnapshot(
    [Object.assign(paper("example2020", 2020), { content_digest: "a".repeat(64) })],
    [{ id: "run-old" }],
    null,
  );
  assert.deepEqual(snapshot.papers, []);
  assert.deepEqual(snapshot.runs, []);
  assert.equal(snapshot.revision, null);
});

test("edge JSON reader bounds the streamed body without Content-Length", async () => {
  const request = new Request("https://example.test/?route=ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: `{"question":"${"x".repeat(1_000_000)}"}`,
  });
  assert.equal(request.headers.has("content-length"), false);
  await assert.rejects(
    readJsonBody(request),
    error => error && error.status === 413 && /exceeds 1 MB/.test(error.message),
  );
});

test("edge client identity ignores spoofed forwarding headers", () => {
  const request = new Request("https://example.test/", {
    headers: {
      "X-Forwarded-For": "1.1.1.1",
      "CF-Connecting-IP": "2.2.2.2",
    },
  });
  assert.equal(publicClientIp(request, { request: { ip: "8.8.8.8" } }), "8.8.8.8");
  assert.equal(publicClientIp(request, {}), "");
});

test("edge handler rejects oversized body before any database work", async () => {
  let queries = 0;
  const ctx = { db: { query: async () => { queries += 1; return { rows: [] }; } } };
  const request = new Request("https://example.test/?route=investigate", {
    method: "POST",
    body: `{"padding":"${"x".repeat(1_000_000)}"}`,
  });
  const response = await handler(request, ctx);
  assert.equal(response.status, 413);
  assert.equal(queries, 0);
});

test("edge handler rate limits public GET routes before graph reads", async () => {
  const queries = [];
  const ctx = {
    request: { ip: "8.8.8.8" },
    db: {
      query: async sql => {
        queries.push(sql);
        if (sql.includes("INSERT INTO api_rate_limits")) {
          return { rows: [{ request_count: 1 }] };
        }
        return { rows: [] };
      },
    },
  };
  const response = await handler(new Request("https://example.test/?route=health"), ctx);
  assert.equal(response.status, 200);
  assert.match(queries[0], /api_rate_limits/);
  assert.equal(queries.length, 4);
});
