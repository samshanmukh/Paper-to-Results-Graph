# HackwithBay 3.0 — Problem Statement & Build Guide

> **Theme:** Thoughtful Agents for Productivity  
> **Format:** Open-ended hackathon — no single mandatory problem, but a **theme** + **partner stack** + **judging criteria**  
> **Source:** Event page, organizer blasts, Butterbase submission schema, partial problem-statement doc (user-provided), HackwithBay 2.0 winning projects (Cerberus, LockIn)

---

## Table of Contents

1. [What You're Actually Being Asked to Build](#what-youre-actually-being-asked-to-build)
2. [What "Thoughtful Agent" Means (Judging Lens)](#what-thoughtful-agent-means-judging-lens)
3. [Partner Technology Stack](#partner-technology-stack)
4. [Suggested Problems](#suggested-problems)
5. [Winning Patterns from HackwithBay 2.0](#winning-patterns-from-hackwithbay-20)
6. [What NOT to Build](#what-not-to-build)
7. [How to Pick Your Idea (Decision Framework)](#how-to-pick-your-idea-decision-framework)
8. [Recommended Architectures by Idea Type](#recommended-architectures-by-idea-type)
9. [Minimum Viable Demo (8 Hours)](#minimum-viable-demo-8-hours)
10. [Scoring & Prizes Cheat Sheet](#scoring--prizes-cheat-sheet)

---

## What You're Actually Being Asked to Build

HackwithBay 3.0 is **not** a fixed coding challenge with one correct answer. Organizers give you:

1. A **theme** — build agents that improve productivity in a *thoughtful* way
2. A **partner stack** — Butterbase, RocketRide, Neo4j (+ optional Daytona, Nebius, Opsera)
3. **Judging criteria** — live demo, real utility, partner integration, agent quality
4. **Submission requirements** — deployed URL, GitHub repo, Butterbase MCP submission

You choose the **specific problem** (whose productivity? what workflow?). The theme tells you *how* to build it (agents, not static apps). The partners tell you *what tools* to use.

**One sentence:** Build an AI agent that helps someone get more done — with memory, tools, and multi-step reasoning — deployed live, using the sponsor stack.

---

## What "Thoughtful Agent" Means (Judging Lens)

A **thoughtful agent** is NOT:

- A ChatGPT wrapper with a custom system prompt
- A single LLM call that returns text
- A dashboard with no agent loop
- Localhost-only with no deployment

A **thoughtful agent** IS:

| Trait | What judges look for | How to show it |
|-------|---------------------|----------------|
| **Plans** | Breaks tasks into steps/waves | RocketRide `agent_rocketride` with `max_waves` > 1 |
| **Remembers** | Context across steps or sessions | `memory_internal`, Neo4j graph, Butterbase DB |
| **Uses tools** | Calls APIs, DBs, code — not just talks | `tool_*`, `db_neo4j`, Butterbase MCP |
| **Reasons over structure** | Relationships matter | Neo4j knowledge graph |
| **Acts, not only advises** | Writes data, triggers workflows | Butterbase functions, Daytona sandboxes |
| **Explains** | User sees what it did and why | UI with agent stages, citations, graph viz |
| **Deploys** | Works for judges without your laptop | RocketRide Cloud + Butterbase frontend URL |

**HackwithBay 2.0 used the same theme.** Winning projects (Cerberus, LockIn) both used **RocketRide wave agents + Neo4j graphs + live deployment**.

---

## Partner Technology Stack

*Reconstructed from the official problem-statement doc (partial) + event partner descriptions.*

### 🧈 Butterbase — Backend-as-a-Service

**What it is:** Spin up and manage backends without boilerplate. AI agents provision apps via MCP.

**Use for:**
- Postgres database + auto REST API
- User auth (email/OAuth) + row-level security
- Serverless functions (custom logic)
- Frontend deployment (live demo URL)
- File storage, RAG, AI gateway
- **Official hackathon submission** channel

**Agent angle:** Your agent creates schema, seeds data, deploys functions and frontend — not you clicking dashboards.

**Prize:** **$200 cash** for best Butterbase use.

**Setup:** MCP in Cursor, promo `ENJOY0707`, enable Developer Mode on app.

---

### 🚀 RocketRide — Open-Source AI Pipeline Runtime

**What it is:** Visual AI pipeline builder (VS Code) with C++ core. **85+ nodes**: 13+ LLM providers, 8+ vector DBs, agents, tools.

**Use for:**
- **Agent orchestration** — `agent_rocketride`, `agent_langchain`, `agent_crewai`
- **Multi-wave reasoning** — parallel tool calls, keyed memory between waves
- **LLM routing** — one pipeline, many providers, without juggling separate API keys and rate limits per provider
- **Tool binding** — HTTP, Python, Git, Butterbase MCP, Neo4j, Daytona, etc.
- **Observability** — token usage, latency, traces in IDE

**Agent angle:** RocketRide is the **brain** — it decides what to query, in what order, with what tools.

---

### 🚀 RocketRide Cloud — Managed Pipeline Hosting

**What it is:** One-click deploy of `.pipe` pipelines to production. Same JSON locally and in cloud.

**Use for:**
- **Live demo URL** for judges (critical!)
- No infrastructure babysitting during the hack
- Promo code in Discord → 100% off first purchase

**Organizer tip:** *"Don't lose demo time to infrastructure — deploy straight from VS Code to a shareable endpoint."*

---

### 🕸️ Neo4j — Native Graph Database

**What it is:** Data as **nodes and relationships**, not tables. Query with **Cypher**. Built-in graph algorithms.

**Natural fits:**
- Social graphs, org charts
- Fraud rings, threat intel
- Recommendation engines
- **Knowledge graphs** for agent context
- Dependency trees, supply chains
- Task/project relationship maps

**Agent angle:** Let your agent **traverse connections** that SQL JOINs express poorly — "what's related to X across 3 hops?"

**In RocketRide:** `db_neo4j` node — natural language → Cypher → graph results.

---

### 📦 Daytona (Optional) — Sandboxes for Agents

**What it is:** Secure, **stateful sandboxes** — full computers with filesystem and kernel. Agent can install deps, write files, run and test code, resume later.

**Use when your agent must:**
- Generate and **execute** code (not just suggest it)
- Process data computationally
- Run multi-step build/test tasks autonomously
- Do something in a real environment, not just talk about it

**In RocketRide:** `tool_daytona` node.

**Skip if:** Your agent only reads/writes APIs and databases — Butterbase + RocketRide may be enough.

---

### ☁️ Nebius (Optional) — GPU Cloud

High-performance GPU infrastructure for training or heavy inference. Use if your idea needs custom model training or large-scale GPU inference beyond standard LLM APIs.

---

### ⚙️ Opsera (Optional) — DevOps Orchestration

AI-driven DevOps platform. Use if your productivity agent touches CI/CD, release automation, or software delivery workflows.

---

## Suggested Problems

*The official doc's "Suggested Problems" section was cut off in the materials available. Below are **theme-aligned problem areas** synthesized from the theme, partner strengths, and HackwithBay 2.0 winners. Pick ONE narrow workflow.*

### Tier 1 — Strong partner fit (recommended)

| # | Problem | User | Agent does | Neo4j? | Daytona? |
|---|---------|------|------------|--------|----------|
| 1 | **Focus / distraction coach** | Knowledge worker | Tracks activity, classifies on/off-task, nudges back, session report | ✅ Sessions → Sites → Patterns | Optional |
| 2 | **Meeting → action items** | Team lead | Ingest transcript/notes, extract tasks, assign owners, persist graph | ✅ People → TASK → Project | Optional |
| 3 | **Research synthesizer** | Analyst / student | Multi-source research, entity extraction, connected brief | ✅ Sources → claims → entities | ✅ Run analysis code |
| 4 | **Threat / fraud investigator** | Security analyst | Cross-domain entity trace, score risk, narrative (Cerberus pattern) | ✅ Required | Optional |
| 5 | **Personal knowledge assistant** | Individual | Capture notes/links, build KG, answer with citations | ✅ Core | No |
| 6 | **Project coordination agent** | Small team | Tasks, dependencies, blockers, "what should I do next?" | ✅ Tasks/deps graph | No |
| 7 | **Onboarding copilot** | New hire | Answer questions from company KB, route to right person/resource | ✅ Org + docs graph | No |
| 8 | **Code review / PR triage** | Developer | Summarize PR, find related issues, suggest reviewers | ✅ Code → deps → people | ✅ Daytona to run tests |

### Tier 2 — Good productivity angles

| # | Problem | Hook |
|---|---------|------|
| 9 | **Email / message drafter** | Agent drafts replies using CRM + past thread graph |
| 10 | **Learning path planner** | Break goal into skills, track progress, adapt plan |
| 11 | **Calendar intelligence** | Prep briefs before meetings from attendee + project graph |
| 12 | **Document Q&A for team** | RAG + graph for "how does X relate to Y?" |
| 13 | **Habit / commitment tracker** | Record commitments, follow up, surface broken promises (Butterbase Substrate pattern) |
| 14 | **Support ticket router** | Classify, link similar tickets, suggest resolution from past graph |

### Tier 3 — Ambitious (high risk in 8 hours)

| # | Problem | Risk |
|---|---------|------|
| 15 | Full autonomous software builder | Scope creep |
| 16 | Multi-tenant SaaS with billing | Too much infra |
| 17 | Real-time collaboration platform | Needs too much polish |

---

## Winning Patterns from HackwithBay 2.0

Same theme: **Thoughtful Agents for Productivity**. Study these repos:

### Cerberus (security / cross-domain investigation)

- **Stack:** RocketRide `agent_rocketride` + Neo4j MCP + `memory_internal` + Python scoring tool + HTTP enrichment
- **Pattern:** Wave-planning agent — parallel tool calls per wave, keyed memory between waves
- **Neo4j:** Core data model — entities and relationships ARE the product
- **Why it won:** Agent truly *reasons* over graph; visible multi-stage UI; self-improvement loop

GitHub: https://github.com/kvn8888/Cerberus

### LockIn (browser focus tracker)

- **Stack:** Chrome extension + FastAPI + Neo4j + RocketRide pipeline + GPT-4o
- **Pattern:** Passive tracking → AI classification → gentle nudges → session report + Q&A
- **Neo4j:** Sessions, visits, sites as graph — pattern detection across time
- **Why it worked:** Clear productivity problem, real-time agent loop, graph-backed insights

GitHub: https://github.com/JNK234/lockin

### Common DNA (copy this)

```
User input → RocketRide agent (waves + memory + tools)
                ├── Neo4j (structure / relationships)
                ├── Butterbase (persistence / auth / API / frontend)
                ├── External APIs (enrichment)
                └── Optional Daytona (code execution)
→ Live UI with deployed URL
→ 2-minute demo tells a story
```

---

## What NOT to Build

| Avoid | Why |
|-------|-----|
| Generic chatbot | Doesn't show "thoughtful" agent |
| localhost-only demo | Judges can't open it; submission needs URL |
| No graph when relationships matter | Wastes Neo4j partner |
| No RocketRide pipeline | Wastes core sponsor; harder to show orchestration |
| No Butterbase backend | Misses $200 prize + submission path |
| Scope: "AI for everything" | 8 hours — one workflow only |
| Manual provisioning during demo | Agent should set up Butterbase, not you clicking |

---

## How to Pick Your Idea (Decision Framework)

Answer these in 5 minutes at kickoff:

### 1. Who is the user? (pick one)
- [ ] Individual (focus, learning, personal KB)
- [ ] Team (meetings, projects, onboarding)
- [ ] Analyst (research, security, support)

### 2. What's the ONE painful moment?
Examples: "I lost focus for 20 min", "Meeting ended with no clear owners", "I can't find how X connects to Y"

### 3. Does it need a graph?
- **Yes** → Neo4j (relationships, hops, patterns over time)
- **No** → Butterbase Postgres + RAG may suffice (simpler)

### 4. Does the agent need to run code?
- **Yes** → Add Daytona
- **No** → Skip Daytona, ship faster

### 5. Can you demo in 90 seconds?
If you can't explain problem → agent action → result in 90 sec, narrow further.

### Quick recommendation if stuck

**Safest winning path for HackwithBay 3.0:**

> **"FocusFlow"** or **"MeetingMind"** — LockIn/Cerberus patterns, well-understood productivity pain, clear Neo4j graph, RocketRide agent, Butterbase UI + API, live deploy.

---

## Recommended Architectures by Idea Type

### Pattern A: Graph-centric agent (Cerberus-style)

```
chat/webhook → agent_rocketride
                  ├── llm_openai (control)
                  ├── memory_internal (control)
                  ├── db_neo4j + llm (control) OR Neo4j MCP
                  ├── tool_http_request (control)
                  └── tool_python (control, optional)
→ response_answers
```

**Butterbase:** Frontend dashboard + store investigation history  
**Deploy:** RocketRide Cloud pipeline URL + Butterbase frontend URL

---

### Pattern B: Passive capture + insights (LockIn-style)

```
webhook (events from extension/app) → parse → agent classifies
                                              → write to Neo4j (via API)
chat (Q&A) → agent_rocketride → db_neo4j → response
```

**Butterbase:** Auth, event ingestion API, report UI  
**Neo4j:** Session / visit / site graph

---

### Pattern C: Backend-first agent (Butterbase-heavy)

```
chat → agent_rocketride
         ├── tool_butterbase (schema, functions, deploy)
         ├── memory_internal
         └── llm
→ Butterbase-deployed React frontend talks to Butterbase API
→ RocketRide Cloud for agent pipeline endpoint
```

**Best for:** $200 Butterbase prize — show agent provisioning real backend.

---

### Pattern D: Research / KB agent

```
dropper/webhook (docs) → parse → preprocessor → embedding → qdrant/chroma
chat → embedding → vector search → prompt → llm → response
Optional: Neo4j for entity links between documents
```

**RocketRide:** `examples/rag-pipeline.pipe` as starting point  
**Add agent layer:** Wrap query path in `agent_rocketride` with tools

---

## Minimum Viable Demo (8 Hours)

### Hour 0–1: Decide + scaffold
- Pick problem from Tier 1 table
- `init_app` on Butterbase
- Copy closest example `.pipe` (butterbase-agent or agent-workflow)
- Fresh `project_id` UUID

### Hour 1–3: Happy path only
- One input → agent runs → one useful output
- Neo4j: 2–3 node types, 2–3 relationship types (don't over-model)
- Skip polish

### Hour 3–5: UI + deploy
- Butterbase frontend (even minimal chat UI)
- RocketRide Cloud deploy
- Verify URLs in incognito

### Hour 5–6: Graph/agent depth
- Add one "wow" moment: graph viz, wave reasoning, or pattern memory
- Second tool or enrichment call

### Hour 6–7: Demo prep
- GitHub README with architecture diagram
- Rehearse 2-min pitch
- Seed demo data

### Hour 7–8: Submit + buffer
- `prep_and_submit_hackathon_entry` via Butterbase MCP
- Fix deploy if broken

---

## Scoring & Prizes Cheat Sheet

| Criterion | How to maximize |
|-----------|-----------------|
| **Theme fit** | Multi-wave agent, memory, tools — explain why it's "thoughtful" |
| **Butterbase** | Real app with schema + auth + deployed frontend → **$200 prize** |
| **RocketRide** | Visible pipeline, Cloud deploy, mention wave orchestration |
| **Neo4j** | Graph is central to the solution, not bolted on |
| **Live demo** | `deployed_project_url` works for judges |
| **Submission** | Butterbase MCP with `app_id` for bonus points |
| **Story** | "Before: 4 hours manual work → After: 30 seconds with agent" |

### Submission fields (reminder)

| Field | Required |
|-------|----------|
| `project_title` | ✅ |
| `team_members_names_all` | ✅ |
| `team_members_emails_all` | ✅ |
| `deployed_project_url` | ✅ **must be live** |
| `phone_number` | ✅ |
| `github_repo` | ✅ |
| `feedback` | ✅ |
| `demo_presentation` | optional |
| `app_id` | strongly recommended |

---

## Partial Official Text (User-Provided Fragment)

*The following is the portion of the official problem-statement document that was available. The beginning (likely Butterbase section) was truncated.*

> …managing separate API keys and rate limits per provider.

### 🕸️ Neo4j — The Native Graph Database

Neo4j stores data as nodes and relationships instead of tables, making it a natural fit for anything relational-at-heart: social graphs, org charts, fraud rings, recommendation engines, knowledge graphs, dependency trees, and supply chains. Query with Cypher, run built-in graph algorithms (shortest path, centrality, community detection), and let your agent traverse connections a SQL join could never express cleanly.

### 🚀 RocketRide Cloud — Managed AI Pipeline Runtime

RocketRide is an open-source AI pipeline builder with a high-performance C++ core, built visually in VS Code and backed by 13+ LLM providers, 8+ vector databases, and multi-agent orchestration support. Pipelines are portable JSON — build them locally, then one-click deploy to RocketRide Cloud (cloud.rocketride.ai) for managed hosting, so your pipeline runs the same way in production as it did on your laptop, with no infrastructure to babysit.

### 📦 Daytona (Optional) — Sandboxes for Agents

Daytona provides secure, stateful sandboxes — full composable computers with their own filesystem and kernel — where an AI agent can install dependencies, write files, run and test code, and pick right back up where it left off in a later session. Ideal for any project where your agent needs to do something computational, not just talk about it: generate and run code, process data, or complete multi-step build tasks autonomously.

### Suggested Probl…

*(document cut off — see [Suggested Problems](#suggested-problems) section above for completed list)*

---

## Related Reference Files

| File | Contents |
|------|----------|
| `reference/hackwithbay-3.0-prep.md` | Logistics, checklist, submission |
| `reference/butterbase-reference.md` | MCP tools, schema, deploy |
| `reference/rocketride-reference.md` | Pipelines, agents, nodes, Cloud |
| `reference/neo4j-reference.md` | Cypher, GraphRAG, Aura |
| `reference/rocketride-server/examples/` | Starter `.pipe` files |

---

## Still Stuck? Use This Pitch Template

> **Problem:** [Specific person] wastes [X time] on [specific task] because [reason].  
> **Solution:** [Product name] is a thoughtful agent that [verb] using multi-step reasoning.  
> **How:** RocketRide orchestrates the agent; Neo4j stores [relationships]; Butterbase powers [auth/API/UI].  
> **Demo:** I'll paste [input] → agent [does waves] → live result at [URL].

Fill in the blanks in 10 minutes at the venue. Build that and nothing else.

---

*Good luck tomorrow. Pick one problem. Deploy by hour 5. Demo live. 🚀*
