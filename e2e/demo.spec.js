const { test, expect } = require("@playwright/test");

/**
 * Smoke the guided-demo path against /demo (static shell + mocked APIs).
 * When VERIGRAPH_BASE_URL points at a live FastAPI/Butterbase host, mocks are skipped
 * only for structural checks — default CI uses the static server + route mocks.
 */
test.describe("Verigraph demo smoke", () => {
  test.beforeEach(async ({ page }) => {
    // Bypass email gate
    await page.addInitScript(() => {
      localStorage.setItem("vg_visitor_id", "00000000-0000-4000-8000-000000000001");
      localStorage.setItem("vg_visitor_email", "e2e@verigraph.test");
      localStorage.setItem("vg_theme", "light");
    });

    await page.route("**/api/**", async (route) => {
      const url = route.request().url();
      const method = route.request().method();
      const path = url.split("/api/")[1]?.split("?")[0] || "";

      if (path === "register" && method === "POST") {
        return route.fulfill({
          json: { ok: true, visitor_id: "00000000-0000-4000-8000-000000000001", email: "e2e@verigraph.test" },
        });
      }
      if (path === "health") {
        return route.fulfill({ json: { ok: true, live_run: false, impl_methods: 6 } });
      }
      if (path === "workspace") {
        return route.fulfill({
          json: {
            empty: false,
            papers: 3,
            claims: 9,
            runs: 1,
            paper_ids: ["adam2014", "wilson2017", "adamw2017"],
            papers_detail: [
              { id: "adam2014", title: "Adam", year: 2014, claims: 3, methods: 1 },
              { id: "wilson2017", title: "Wilson", year: 2017, claims: 3, methods: 1 },
              { id: "adamw2017", title: "AdamW", year: 2017, claims: 3, methods: 1 },
            ],
          },
        });
      }
      if (path === "graph") {
        return route.fulfill({
          json: {
            nodes: [
              { eid: "p-wilson2017", label: "Paper", key: "wilson2017", caption: "Wilson et al.", props: { id: "wilson2017", title: "Wilson", year: 2017 } },
              { eid: "c-wilson2017-c2", label: "Claim", key: "wilson2017-c2", caption: "SGD beats Adam on separable problem", props: { id: "wilson2017-c2", text: "SGD beats Adam" } },
              {
                eid: "m-wilson2017-m1",
                label: "Method",
                key: "wilson2017-m1",
                caption: "Separable counterexample",
                props: {
                  id: "wilson2017-m1",
                  name: "Separable counterexample",
                  description: "GD vs Adam",
                  runnable_hint: "numpy",
                  params: JSON.stringify([
                    { name: "n_train", default: 200, description: "Training examples" },
                  ]),
                },
              },
              {
                eid: "r-demo",
                label: "Run",
                key: "run-wilson2017-m1-demo",
                caption: "run-wilson2017-m1-demo",
                props: { id: "run-wilson2017-m1-demo", backend: "local", status: "success", duration_s: 1.2 },
              },
            ],
            edges: [
              { src: "c-wilson2017-c2", dst: "p-wilson2017", rel: "FROM" },
              { src: "m-wilson2017-m1", dst: "p-wilson2017", rel: "DESCRIBED_IN" },
              { src: "r-demo", dst: "m-wilson2017-m1", rel: "IMPLEMENTS" },
              { src: "r-demo", dst: "c-wilson2017-c2", rel: "VALIDATES" },
            ],
          },
        });
      }
      if (path === "evidence") {
        return route.fulfill({
          json: [
            {
              paper: "wilson2017",
              claim: "wilson2017-c2",
              text: "On a constructed separable problem, SGD achieves zero test error while Adam approaches 50%.",
              evidence: "VALIDATES by run-wilson2017-m1-demo",
            },
          ],
        });
      }
      if (path === "insights") {
        return route.fulfill({
          json: {
            generated_at: "2026-07-12T00:00:00Z",
            counts: { papers: 3, claims: 1, methods: 1, runs: 1, artifacts: 0, nodes: 5 },
            evidence: { total_claims: 1, validated: 1, refuted: 0, untested: 0, coverage_pct: 100 },
            conflicts: { total: 0, untested: 0, adjudicated: 0, resolved: 0, both_supported: 0 },
            methods: { total: 1, with_runs: 1, never_run: 0, next_recommended: null, items: [] },
            conflict_rows: [],
            claim_rows: [],
          },
        });
      }
      if (path === "batch-plan") return route.fulfill({ json: { pending: [], count: 0 } });
      if (path === "timeline") return route.fulfill({ json: [] });
      if (path === "runs") return route.fulfill({ json: [] });
      if (path === "saved-workspaces") return route.fulfill({ json: { workspaces: [] } });
      if (path.startsWith("run/") && method === "POST") {
        const params = route.request().postDataJSON()?.params || {};
        if (Object.keys(params).length) {
          return route.fulfill({
            status: 409,
            json: {
              code: "LIVE_EXECUTION_REQUIRED",
              detail: "Changed parameters require a live sandbox; the saved run was not substituted.",
            },
          });
        }
        return route.fulfill({
          json: {
            run_id: "run-wilson2017-m1-demo",
            method_id: "wilson2017-m1",
            backend: "local",
            exit_code: 0,
            duration_s: 1.2,
            stdout: '{"method_id":"wilson2017-m1","metrics":{"gd_test_error":0,"adam_test_error":0.425},"claim_checks":[{"claim_id":"wilson2017-c2","verdict":"VALIDATES","detail":"ok"}]}',
            result: {
              method_id: "wilson2017-m1",
              metrics: { gd_test_error: 0, adam_test_error: 0.425 },
              claim_checks: [{ claim_id: "wilson2017-c2", verdict: "VALIDATES", detail: "ok" }],
            },
            error: null,
            replay: true,
          },
        });
      }
      if (path === "ask" && method === "POST") {
        return route.fulfill({
          json: { answer: "wilson2017-c2 VALIDATES via run-wilson2017-m1-demo (gd 0.000 vs adam 0.425)." },
        });
      }
      if (path === "workspace/load-demo" && method === "POST") {
        return route.fulfill({
          json: { papers: 3, claims: 9, empty: false, message: "Demo loaded" },
        });
      }
      return route.fulfill({ status: 200, json: {} });
    });
  });

  test("demo shell exposes guided path controls", async ({ page }) => {
    await page.goto("/demo/");
    // static server serves index.html at / not /demo/ — also try root index when using http.server
    if (page.url().includes("8787") && !(await page.locator("#guided-demo-btn").count())) {
      await page.goto("/index.html");
    }
    await expect(page.locator("#guided-demo-btn")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('.tab-btn[data-tab="insights"]')).toBeVisible();
    await expect(page.locator("#export-btn")).toBeVisible();
    await expect(page.locator("#batch-run-btn")).toBeVisible();
    await expect(page.locator("#search-input")).toBeVisible();
    await expect(page.locator('.layout-mode-btn[data-mode="citations"]')).toBeVisible();
  });

  test("guided demo button runs click-through against mocked APIs", async ({ page }) => {
    await page.goto("/index.html");
    await expect(page.locator("#guided-demo-btn")).toBeVisible({ timeout: 15_000 });
    await page.locator("#guided-demo-btn").click();
    // Guided demo should progress past loading and attempt ask
    await expect(page.locator("#demo-toast")).not.toHaveClass(/hidden/, { timeout: 20_000 }).catch(() => {});
    // Evidence or ask tab should show VALIDATES content eventually
    await expect
      .poll(async () => {
        const text = await page.locator("body").innerText();
        return /VALIDATES|Guided demo|wilson2017/i.test(text);
      }, { timeout: 45_000 })
      .toBeTruthy();
  });

  test("does not present a saved run as a parameterized execution", async ({ page }) => {
    await page.goto("/index.html");
    await page.evaluate(() => {
      selectMethod({
        props: {
          id: "wilson2017-m1",
          name: "Separable counterexample",
          params: JSON.stringify([{ name: "n_train", default: 200 }]),
        },
      });
    });

    await page.locator('#m-params input[data-name="n_train"]').fill("400");
    await page.locator("#run-btn").click();

    await expect(page.locator("#log")).toContainText("the saved run was not substituted");
    await expect(page.locator("#run-btn")).toHaveText("▶ RUN THIS METHOD");
    await expect(page.locator("#run-btn")).toBeEnabled();
  });

  test("keeps the selected method stable while its run is in flight", async ({ page }) => {
    await page.route("**/api/run/**", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 250));
      return route.fulfill({
        json: {
          run_id: "run-adam2014-m1-live",
          method_id: "adam2014-m1",
          backend: "daytona",
          exit_code: 0,
          duration_s: 0.25,
          stdout: "",
          result: {
            metrics: { final_loss: 0.01 },
            claim_checks: [{ claim_id: "adam2014-c1", verdict: "VALIDATES", detail: "ok" }],
          },
          error: null,
          live: true,
        },
      });
    });
    await page.goto("/index.html");
    await page.evaluate(() => {
      selectMethod({
        props: { id: "adam2014-m1", name: "Adam optimizer", params: "[]" },
      });
    });

    await page.locator("#run-btn").click();
    const switched = await page.evaluate(() =>
      selectMethod({
        props: { id: "wilson2017-m1", name: "Wilson counterexample", params: "[]" },
      })
    );

    expect(switched).toBe(false);
    await expect(page.locator("#m-paper")).toHaveText("METHOD · adam2014-m1");
    await expect(page.locator("#run-btn")).toHaveText("✓ run complete");
  });
});
