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
| DAYTONA_API_KEY | ❌ MISSING (empty in sceneshop/.env too) | user must provide in `.env` |
| OPENAI/ANTHROPIC key | optional | only as fallback; prefer RocketRide routing |

## Blockers (loop appends here; user resolves)

- DAYTONA_API_KEY not set — M4 sandbox execution blocked until provided; build local-subprocess fallback runner meanwhile.

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

### M3 — Codegen (needs LLM key; mock fallback = pre-written implementation)
- [ ] `app/codegen.py`: method node → small runnable Python implementation (single file, stdlib+numpy only, prints a JSON metric line at the end)
- [ ] Pre-write one known-good implementation for the demo method as `papers/impl/` fallback

### M4 — Sandbox execution (BLOCKED: DAYTONA_API_KEY)
- [ ] `app/runner.py`: Daytona SDK — create sandbox, upload code, run, capture stdout/stderr/exit code, parse metric JSON
- [ ] Local-subprocess fallback runner so the demo works even if Daytona is down

### M5 — Close the loop
- [ ] `app/curator.py`: write Run + Artifact nodes back to Neo4j, link Run-[:IMPLEMENTS]->Method and Run-[:VALIDATES|REFUTES]->Claim
- [ ] End-to-end script `scripts/demo_loop.py`: pick method → codegen → run → curate → print graph diff
- [ ] Verify: "which claims now have executable evidence?" query returns the new Run

### M6 — RocketRide orchestration
- [ ] Read ALL RocketRide docs first (ground rules)
- [ ] `pipelines/paper2result.pipe`: orchestrates extract → generate → run → update-graph (agent + tools per component reference)
- [ ] Validate pipeline locally per RocketRide quickstart

### M7 — UI + Butterbase (BLOCKED: Butterbase account)
- [ ] Minimal web UI: graph viz (papers/claims/methods/runs), "Run this method" button, live run log panel
- [ ] Butterbase backend: store papers, runs, artifacts; deploy frontend for live URL
- [ ] Fallback: local FastAPI + static page with vis-network so demo works regardless

### M8 — Demo + submission
- [ ] Seed demo data end-to-end; rehearse the 2-min flow from README
- [ ] Update README status table; architecture diagram
- [ ] Butterbase MCP submission (`prep_and_submit_hackathon_entry`) [BLOCKED: Butterbase]

## Iteration Log

(loop appends: iteration #, what was done, what's verified, what's next)

- **#3 (2026-07-07):** M2 complete. Graph live in Aura with full schema; conflicts query surfaces 4 real cross-paper contradictions. Discovered the Aura instance is shared with sceneshop — all destructive ops restricted to our labels via `OUR_LABELS` in `app/db.py`. Next: M3 codegen (pre-written wilson2017-m1 implementation first, since it's the demo centerpiece and needs no LLM).
- **#2 (2026-07-07):** M1 complete. Demo topic locked: "Do adaptive optimizers beat SGD?" (Adam vs Wilson-et-al critique vs AdamW — real conflicting claims, sandbox-runnable methods). `extract.py --mock` validates all 3 golden extractions. The Wilson separable-counterexample method (`wilson2017-m1`) is the designated demo method — tiny numpy experiment with a dramatic result (SGD 0% vs Adam ~50% test error). Next: M2 — Neo4j graph loader (inspect the 36 pre-existing nodes first).
- **#1 (2026-07-07):** M0 complete. Scaffolded dirs, `.env` (Neo4j + Butterbase + RocketRide local), venv with deps, Neo4j Aura smoke test passing (36 pre-existing nodes in instance — inspect/clear before M2 load). Daytona key still missing. Next: M1 — preload papers + extraction (route LLM via local RocketRide engine per user directive; keep `--mock` fallback).
