# Paper-to-Results Graph — Autonomous Build Loop State

This file is the single source of truth for the build loop. Each loop iteration:
1. Reads this file top to bottom.
2. Picks the FIRST unchecked task whose dependencies are met.
3. Does the work (small, verifiable increments — run/test everything you build).
4. Updates checkboxes + the Iteration Log at the bottom, then ends the iteration.

## Ground rules for every iteration

- **Read the RocketRide docs before writing any RocketRide code**: `.rocketride/docs/ROCKETRIDE_README.md`, `ROCKETRIDE_QUICKSTART.md`, `ROCKETRIDE_PIPELINE_RULES.md`, `ROCKETRIDE_COMPONENT_REFERENCE.md`, `ROCKETRIDE_COMMON_MISTAKES.md` (in the workspace root `.rocketride/docs/`).
- Sponsor references live in `reference/` (neo4j-reference.md, butterbase-reference.md, rocketride-reference.md, hackwithbay-3.0-prep.md, problem-statement.md, rocketride-server/ examples).
- Scope discipline: build ONE closed loop — one paper → one method → one runnable experiment → one result → graph update. Nothing more.
- Verify as you go: every component gets a smoke test before its box is checked.
- If blocked on a missing credential, mark the task `[BLOCKED: <what>]`, add it to "Blockers" below, and move to the next unblocked task. NEVER invent keys.
- Commit to git after each completed milestone with a clear message, then **push to GitHub** (`git push origin HEAD` — branch is `master`).

## User directives (2026-07-07)

- **Ignore `reference/rocketride-server/`** — we use RocketRide LOCALLY only. Local engine confirmed running at `http://localhost:5565` (webhook: `PIPELINE_URL` in `.env`).
- Shared API keys come from `../sceneshop/.env` (already merged into this project's `.env`).
- LLM calls route through the local RocketRide engine — no direct OpenAI/Anthropic key needed.

## Credentials

| Key | Status | Where |
|-----|--------|-------|
| NEO4J_URI / USER / PASSWORD | ✅ verified (36 nodes) | `.env` (Aura instance d0b99ba9) |
| BUTTERBASE_API_KEY / APP_ID / SERVICE_KEY / MCP_URL | ✅ in `.env` | from `../sceneshop/.env` |
| RocketRide local engine | ✅ running on :5565 | `PIPELINE_URL` in `.env` |
| DAYTONA_API_KEY | ✅ provided + verified (sandbox run 4.9s) | `.env` |
| LLM access | ✅ SOLVED via Butterbase AI gateway | OpenAI-compatible: `$BUTTERBASE_API_URL/v1/$APP_ID/chat/completions`, Bearer = SERVICE_KEY, model `anthropic/claude-sonnet-4.5` (verified). Wired as ROCKETRIDE_GATEWAY_* in .env |

## Blockers (loop appends here; user resolves)

- (resolved) DAYTONA_API_KEY provided 2026-07-07; sandbox execution verified.

## Milestones

### M0 — Scaffold
- [x] Create project structure: `app/` (backend), `pipelines/` (RocketRide .pipe), `papers/` (preloaded PDFs/text), `scripts/`, `.env.example`, `.env` (gitignored), `requirements.txt`
- [x] Write `.env` with Neo4j creds + Butterbase/RocketRide keys from sceneshop
- [x] Verify Neo4j Aura connectivity with a Python smoke test (`scripts/check_neo4j.py`) — passing; NOTE: must set `SSL_CERT_FILE` to certifi bundle before neo4j driver connects (macOS Python lacks system CAs)

### M1 — Papers + extraction
- [x] Preload 3 papers on topic "adaptive optimizers vs SGD": `adam2014`, `wilson2017`, `adamw2017` (condensed excerpts in `papers/*.txt`) — claims genuinely conflict, methods runnable in numpy
- [x] `app/extract.py`: schema validation + `--mock` mode working; live mode intentionally deferred to M6 (routes via local RocketRide pipeline)
- [x] Golden extraction JSON in `papers/extracted/` — 9 claims, 3 methods, 4 cross-paper CONTRADICTS relations, citation edges

### M2 — Knowledge graph (Neo4j)
- [x] `app/graph.py`: idempotent MERGE loader + `app/db.py` shared driver. ⚠️ Aura instance is SHARED with sceneshop (their 36 nodes: SceneStyle/Product/Creator/...) — `--reset` only deletes OUR_LABELS; NEVER run label-less deletes
- [x] Loaded: 3 Paper, 9 Author, 9 Claim, 3 Method, 3 Dataset, 1 Task; edges WROTE 9, FROM 9, CITES 3, CONTRADICTS 4, DESCRIBED_IN 3, EVALUATED_ON 4, ADDRESSES 3
- [x] `app/queries.py`: claims / conflicts / methods / evidence queries verified (evidence shows "no runs yet" — flips after M5)

### M3 — Codegen
- [x] `app/codegen.py`: curated-first codegen (papers/impl/ → generated/), output contract = last stdout line is JSON {method_id, metrics, claim_checks[{claim_id, verdict, detail}]}; live LLM path deferred to M6
- [x] `papers/impl/wilson2017-m1.py` verified: GD test error 0.000 vs Adam 0.425 (paper's failure mode reproduced — Adam weights equalize). Both wilson claims VALIDATE. Key tuning: class imbalance P_POS=0.6 + full-batch training required to reproduce the effect

### M4 — Sandbox execution
- [x] `app/runner.py` local backend verified: materialize → run → capture → `runs/<run_id>.json` record → claim verdicts printed. Failures are captured as data (error field), not crashes
- [x] Daytona backend VERIFIED live (2026-07-07): real sandbox run in 4.9s, both wilson claims VALIDATE; full closed loop via daytona confirmed. Note: org must have a default region set in the Daytona Dashboard (API cannot set it). `--backend auto` now picks daytona

### M5 — Close the loop
- [x] `app/curator.py`: Run + Artifact (stdout, capped 4k chars) nodes, IMPLEMENTS/VALIDATES/REFUTES edges with detail; failed runs curated too
- [x] `scripts/demo_loop.py`: method → codegen → run → curate → evidence diff
- [x] VERIFIED CLOSED: evidence flipped 'no runs yet' → 'VALIDATES by run-wilson2017-m1-...' for both wilson claims. This is the demo command: `python scripts/demo_loop.py wilson2017-m1`

### M6 — RocketRide orchestration
- [x] Read RocketRide docs (README, QUICKSTART, PIPELINE_RULES, COMPONENT_REFERENCE, COMMON_MISTAKES) + services-catalog + schemas for llm_openai_api/db_neo4j/tool_python/tool_daytona
- [x] `pipelines/paper2result.pipe`: chat → agent_rocketride (waves+memory) → response_answers; agent controls llm_openai_api (Butterbase AI gateway!), memory_internal, db_neo4j (Aura), tool_python. db_neo4j shares the same LLM node for NL→Cypher
- [x] VERIFIED live via `scripts/check_pipeline.py`: agent answered "which claims have executable evidence" citing real run id, VALIDATES verdicts, metrics from the graph
- [ ] Polish: agent sometimes misquotes metric values (said 0.05 vs actual 0.425) — tighten instructions or db_description; add tool_daytona node when key arrives

### M7 — UI + Butterbase
- [x] Demo UI (`static/index.html` + `app/server.py` FastAPI on :8787): vis-network graph (color-coded node types, VALIDATES/REFUTES/CONTRADICTS edge styling), method panel + RUN button, experiment console with staged log, evidence table, ask-the-agent box (RocketRide pipeline). Verified in Chrome: click method → RUN → console streams verdicts → new Run node appears → evidence flips
- [x] Butterbase backend: dedicated app `paper2result` (app_vinjruy5c03s, https://paper2result.butterbase.dev) created via /init — sceneshop's app untouched. Tables papers+runs applied; `app/butterbase.py` sync (insert-or-skip; jsonb arrays must be wrapped in an object; row-id endpoints need UUID ids so text-id rows are immutable). Server mirrors every run to Butterbase automatically
- [ ] Deployed frontend URL: DECISION NEEDED — UI needs the local FastAPI backend (graph/run/ask), so a Butterbase static deploy would need a read-from-Butterbase-only variant or the demo stays on localhost. Note: one stray `probe1` row in runs table (undeletable via REST, text id) — filter out if it shows anywhere

### M8 — Demo + submission
- [x] `scripts/reset_demo.py`: pristine pre-demo state (Run/Artifact cleared, papers reloaded, evidence all 'no runs yet') — verified, then loop re-closed cleanly
- [x] Agent accuracy rule added to pipeline instructions (exact metric quoting)
- [x] README rewritten: demo script, architecture diagram, sponsor mapping, repo map, verified status table
- [ ] Deployed public URL [BLOCKED: user decision — static Butterbase variant vs tunnel vs localhost demo]
- [ ] Hackathon submission [BLOCKED: user input — team names/emails/phone; plus deployed URL]
- [ ] Daytona live verification [BLOCKED: DAYTONA_API_KEY]

## Iteration Log

(loop appends: iteration #, what was done, what's verified, what's next)

- **#9 (2026-07-07):** M8 buildable parts complete (reset script, agent accuracy rule, README). ALL remaining tasks are user-blocked: (1) DAYTONA_API_KEY, (2) deploy decision for live URL, (3) submission details (team names/emails/phone). Loop pausing — restart with /loop or just answer the blockers in chat.
- **#8 (2026-07-07):** M7 complete (minus deploy decision). Instrument-panel UI verified live in Chrome — the killer demo moment (click method → RUN → verdicts stream → graph grows → evidence flips) works visually. Created dedicated Butterbase app paper2result (NOT sceneshop's); papers+runs persisted; server auto-mirrors runs. Butterbase REST quirks learned: primaryKey not primary; jsonb top-level arrays rejected; row-id routes UUID-only. Next: M8 demo prep + README + submission; deploy decision for live URL.
- **#7 (2026-07-07):** M6 complete. KEY DISCOVERY: Butterbase AI gateway is OpenAI-compatible and our service key works — LLM access unblocked without any OpenAI/Anthropic key (also strengthens Butterbase integration story). Pipeline paper2result.pipe verified live: agent → gateway LLM + memory + db_neo4j(Aura) + tool_python, answers evidence questions citing real run ids. extract.py/codegen.py live modes can now also use the gateway (optional polish). Next: M7 UI + Butterbase backend.
- **#6 (2026-07-07):** M5 complete — THE CORE LOOP IS CLOSED end-to-end (paper → method → code → run → result → graph update, verified by evidence-query diff). Everything from here is orchestration + presentation. Next: M6 RocketRide pipeline — MUST read .rocketride/docs (README, QUICKSTART, PIPELINE_RULES, COMPONENT_REFERENCE, COMMON_MISTAKES, python_API) before writing the .pipe.
- **#5 (2026-07-07):** M4 complete (local verified; Daytona code-complete, blocked on key). Run records persist to runs/ with full stdout/stderr/duration + parsed claim verdicts. Inspected installed daytona_sdk 0.194 to write against the real API instead of guessing. Next: M5 — curator writes Run/Artifact nodes back to Neo4j + end-to-end demo_loop.py.
- **#4 (2026-07-07):** M3 complete. First naive reproduction gave 0/0 test error — fixed by matching the paper's conditions (imbalanced classes, full-batch); now GD 0.000 vs Adam 0.425 with Adam's first three weights exactly equalized as the theory predicts. codegen --run validates the JSON contract. Next: M4 runner — local-subprocess fallback is unblocked; Daytona path stays BLOCKED on DAYTONA_API_KEY.
- **#3 (2026-07-07):** M2 complete. Graph live in Aura with full schema; conflicts query surfaces 4 real cross-paper contradictions. Discovered the Aura instance is shared with sceneshop — all destructive ops restricted to our labels via `OUR_LABELS` in `app/db.py`. Next: M3 codegen (pre-written wilson2017-m1 implementation first, since it's the demo centerpiece and needs no LLM).
- **#2 (2026-07-07):** M1 complete. Demo topic locked: "Do adaptive optimizers beat SGD?" (Adam vs Wilson-et-al critique vs AdamW — real conflicting claims, sandbox-runnable methods). `extract.py --mock` validates all 3 golden extractions. The Wilson separable-counterexample method (`wilson2017-m1`) is the designated demo method — tiny numpy experiment with a dramatic result (SGD 0% vs Adam ~50% test error). Next: M2 — Neo4j graph loader (inspect the 36 pre-existing nodes first).
- **#1 (2026-07-07):** M0 complete. Scaffolded dirs, `.env` (Neo4j + Butterbase + RocketRide local), venv with deps, Neo4j Aura smoke test passing (36 pre-existing nodes in instance — inspect/clear before M2 load). Daytona key still missing. Next: M1 — preload papers + extraction (route LLM via local RocketRide engine per user directive; keep `--mock` fallback).
