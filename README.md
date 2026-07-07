# Verigraph

**Turn research papers into executable evidence.**

Most research tools stop at summaries. **Verigraph** closes the loop between what papers *claim* and what actually *runs*:

> **Paper → Claim → Method → Code → Sandbox Run → Result → Graph Update**

Select a method from a paper, generate a runnable implementation, execute it, and watch the result attach itself to the knowledge graph — `VALIDATES` or `REFUTES`, with logs, metrics, and failure data. The graph doesn't just know what papers say. It knows what has been tested.

Built for **HackwithBay 3.0** (July 7, 2026) — *Thoughtful Agents for Productivity*.

---

## The demo (2 minutes)

Three real papers that genuinely disagree are preloaded:

| Paper | Claim (short) |
|---|---|
| **Adam** (Kingma & Ba, 1412.6980) | Adam converges faster; defaults need no tuning |
| **Wilson et al.** (1705.08292) | Adaptive methods **generalize worse** than SGD — on a constructed separable problem SGD gets 0% test error, Adam ~50% |
| **AdamW** (Loshchilov & Hutter, 1711.05101) | Decoupled weight decay fixes Adam's generalization |

The graph shows 4 cross-paper `CONTRADICTS` edges between their claims.

1. Open the UI at `/demo` — graph of papers, claims, methods, citations, conflicts. Evidence table: *"no runs yet"* on all claims.
2. Click the violet **Linearly separable counterexample** method node → **▶ RUN THIS METHOD**.
3. Watch the experiment console: code generated → executed → `VALIDATES wilson2017-c2 — GD test error 0.000 vs Adam 0.425`.
4. A new ⚡ **Run** node appears in the graph, wired `IMPLEMENTS`→Method and `VALIDATES`→Claims. The evidence table flips live.
5. Ask the agent: *"Which claims now have executable evidence?"* — it queries Neo4j and answers with run ids and exact metrics.

**The result actually reproduces the paper**: Adam's weights equalize exactly as Wilson et al.'s theory predicts (we verified digit-for-digit).

---

## Architecture

```
                       ┌─────────────────────────────────────────────┐
                       │         Verigraph UI (static/)            │
                       │  graph viz · RUN button · console · ask box │
                       └──────────────────┬──────────────────────────┘
                                          │ FastAPI (app/server.py)
        ┌─────────────┬───────────────────┼────────────────┬──────────────┐
        ▼             ▼                   ▼                ▼              ▼
   /api/graph    /api/evidence      /api/run/{method}   /api/ask     (static)
        │             │                   │                │
        │             │       codegen → runner → curator   │
        ▼             ▼           │  (Daytona sandbox      ▼
   ┌─────────────────────┐        │   or local fallback)  RocketRide engine
   │     Neo4j Aura      │◄───────┘                       (localhost:5565)
   │  papers · claims ·  │                                 agent_rocketride
   │  methods · runs ·   │◄────── db_neo4j (NL→Cypher) ────┤  + memory
   │  VALIDATES/REFUTES  │                                 │  + tool_python
   └─────────────────────┘        LLM: Butterbase AI ──────┘
                                  gateway (x-ai/grok-4.3)
   ┌─────────────────────┐
   │  Butterbase app     │◄────── every run auto-mirrored
   │  (paper2result)     │        (papers + runs tables)
   └─────────────────────┘
```

### Sponsor stack

| Partner | How it's used |
|---|---|
| **RocketRide** (local engine) | `pipelines/verigraph.pipe` — wave agent with internal memory, `db_neo4j` tool (natural language → Cypher over the research graph), `tool_python`; LLM routed through one node |
| **Neo4j Aura** | The product *is* the graph: papers, claims, methods, runs, `CONTRADICTS`/`VALIDATES`/`REFUTES` edges |
| **Butterbase** | AI gateway (OpenAI-compatible endpoint powers ALL LLM calls) + dedicated app storing papers & run history |
| **Daytona** | Sandbox backend in `app/runner.py` (auto-selected when `DAYTONA_API_KEY` is set; local subprocess fallback keeps the demo unkillable) |

---

## Repo map

```
app/
  extract.py     paper text → {claims, methods, datasets, citations} JSON
  graph.py       idempotent Neo4j loader (MERGE everything)
  queries.py     canned queries: claims / conflicts / methods / evidence
  codegen.py     method node → runnable single-file experiment
  runner.py      execute (daytona | local), capture, persist run record
  curator.py     write Run/Artifact + IMPLEMENTS/VALIDATES/REFUTES back
  butterbase.py  papers + run history mirrored to Butterbase
  server.py      FastAPI backing the UI
  db.py          shared driver (Aura is shared with another project —
                 all destructive ops are label-scoped)
papers/          3 paper excerpts + golden extractions + curated impls
pipelines/       verigraph.pipe (RocketRide agent pipeline)
scripts/         check_neo4j / check_pipeline / demo_loop / reset_demo
static/          landing page + live demo UI
BUILD_LOOP.md    autonomous build log (every milestone verified)
```

---

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in: Neo4j, Butterbase, ROCKETRIDE_*
                       # optional: DAYTONA_API_KEY for real sandboxes

.venv/bin/python scripts/check_neo4j.py      # graph connectivity
.venv/bin/python app/graph.py --reset        # load the 3 papers
.venv/bin/python scripts/check_pipeline.py   # RocketRide agent smoke test

.venv/bin/uvicorn app.server:app --port 8787 # → http://localhost:8787
```

One-command closed loop without the UI:

```bash
.venv/bin/python scripts/demo_loop.py wilson2017-m1
# === graph diff ===
#   wilson2017-c1: 'no runs yet' -> 'VALIDATES by run-wilson2017-m1-...'
#   wilson2017-c2: 'no runs yet' -> 'VALIDATES by run-wilson2017-m1-...'
# loop CLOSED ✓
```

Before a live demo: `.venv/bin/python scripts/reset_demo.py` (pristine "no runs yet" state).

---

## Status

| Component | Status |
|---|---|
| Extraction (3 papers, golden JSON + schema validation) | ✅ verified |
| Neo4j knowledge graph (conflicts + evidence queries) | ✅ verified |
| Codegen + Wilson counterexample (paper effect reproduced) | ✅ verified |
| Runner — local backend | ✅ verified |
| Runner — Daytona backend | ✅ verified (real sandbox, 4.9s) |
| Closed loop (run → graph update → evidence flip) | ✅ verified |
| RocketRide agent pipeline (graph Q&A with citations) | ✅ verified |
| Demo UI | ✅ verified in browser |
| Butterbase (AI gateway + papers/runs persistence) | ✅ verified |
| Deployed public URL | 🔶 pending deploy decision |

## Why this beats "Paper2Code"

Paper2Code stops at code generation. Verigraph runs the code, and the outcome — success, failure, error, metric — becomes a first-class graph citizen attached to the claims it tests. Failed runs are evidence too.

> Research should not end at reading. It should end in evidence.
