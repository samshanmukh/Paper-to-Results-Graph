# HackwithBay 3.0 — Event & Prep Guide

> **Theme:** Thoughtful Agents for Productivity  
> **When:** Tuesday, July 7, 2026 · 9:30 AM – ~5:30–6:30 PM (8-hour hack)  
> **Where:** AWS Builder Loft, 525 Market St, San Francisco, CA — **2nd floor**  
> **You're registered.** Arrive by **9:55 AM** with physical ID.

---

## Tonight's Checklist (Do Before You Sleep)

- [ ] Complete AWS venue access: http://events.builder.aws.com/d/hdz47p
- [ ] Bring **valid physical ID** tomorrow
- [ ] Join **WhatsApp** for live announcements during the event
- [ ] Butterbase MCP configured in Cursor (`~/.cursor/mcp.json`) — restart Cursor
- [ ] Butterbase promo **ENJOY0707** applied at https://dashboard.butterbase.ai/billing (Launch plan)
- [ ] RocketRide Cloud account + promo code from Discord (https://discord.com/invite/PMXrtenMsY)
- [ ] Read problem statement doc (from event organizers — "Review the Doc Here" in Luma)
- [ ] Charge laptop, pack charger, headphones optional

---

## Tomorrow Morning Timeline

| Time | What |
|------|------|
| **9:30 AM** | Event start |
| **9:40 AM** | Doors open / check-in |
| **9:55 AM** | **Latest arrival** — after this you may miss kickoff |
| **~10:00 AM** | Hacking begins (estimate after intros) |
| **~4:00–5:00 PM** | Demo prep / submission window |
| **5:30–6:30 PM** | Demos, judging, awards |

**Venue:** AWS Builder Loft, 2nd floor — [Google Maps](https://maps.google.com/?q=525+Market+St+San+Francisco+CA+94105)

---

## Theme: Thoughtful Agents for Productivity

Build something where **AI agents** help people be more productive — not just chatbots, but agents that:

- Plan, delegate, remember context across steps
- Connect to real data and tools
- Produce **deployable, demo-able** outcomes

**Judges will care about:** live deployed URL, thoughtful agent design, partner tech integration, real utility.

---

## Partners, Prizes & What to Use

| Partner | Role | Prize / Perk | How to use |
|---------|------|--------------|------------|
| **Butterbase** | Backend sponsor | **$200 cash** — best use of Butterbase | MCP in Cursor → `init_app`, schema, auth, functions, **deploy frontend**. **Submit project via Butterbase MCP.** |
| **RocketRide** | AI pipeline sponsor | Free **RocketRide Cloud** credits (promo in Discord) | Build agent pipelines in VS Code, deploy to **live shareable URL** — don't demo localhost |
| **Neo4j** | Knowledge partner | Credibility / graph use cases | Knowledge graphs, GraphRAG, entity relationships via `db_neo4j` node or Aura |
| **Daytona** | Dev environments | Fast sandboxed coding | Instant dev envs for sprint |
| **Nebius** | GPU cloud | Compute for training/inference | GPU workloads if needed |
| **Opsera** | DevOps | CI/CD orchestration | Pipeline automation angle |

### Promo codes

| Code | Where | What |
|------|-------|------|
| **ENJOY0707** | https://dashboard.butterbase.ai/billing | $20 off Launch plan (ALL CAPS) |
| RocketRide promo | Discord https://discord.com/invite/PMXrtenMsY | 100% off first RocketRide Cloud purchase |

### Other perks (from RSVP form)

- $25 Lovable/n8n credits (if you filled https://forms.gle/5YDmGqucsNuRTD8D9)

---

## Submission (via Butterbase MCP)

**All projects submit through Butterbase MCP** — not a web form.

**Hackathon slug:** `HackwithBay-0707`  
**Deadline:** July 11, 2026 (you can submit after the event day)

### Required fields

| Field | Type | Notes |
|-------|------|-------|
| `project_title` | text | Your project name |
| `team_members_names_all` | text | All teammate names |
| `team_members_emails_all` | text | All teammate emails |
| `deployed_project_url` | **URL** | **Live link — not localhost** |
| `phone_number` | text | Contact number |
| `github_repo` | text | Public repo URL |
| `feedback` | text | Event feedback (required) |
| `demo_presentation` | text | Optional slides/video link |

**Scoring tip:** Include your **`app_id`** when submitting — up to **50 bonus points** for Butterbase usage on that app.

### Submit command (when ready)

Ask Cursor/agent to call:

```
prep_and_submit_hackathon_entry({
  action: "submit",
  hackathon_slug: "HackwithBay-0707",
  app_id: "app_your_id_here",
  data: {
    project_title: "...",
    team_members_names_all: "...",
    team_members_emails_all: "...",
    deployed_project_url: "https://...",
    phone_number: "...",
    github_repo: "https://github.com/...",
    demo_presentation: "...",
    feedback: "..."
  }
})
```

---

## Recommended Tech Stack (Aligned with Partners)

```
┌─────────────────────────────────────────────────────────┐
│  Frontend (Butterbase-hosted or Vercel)                 │
│  Live URL for judges ← deployed_project_url             │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│  Butterbase Backend                                     │
│  • Postgres + auto REST API                             │
│  • Auth (email/OAuth) + RLS                             │
│  • Serverless functions for custom logic                │
│  • Optional: RAG, AI gateway                            │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│  RocketRide Pipeline (agent brain)                      │
│  chat → agent_rocketride                                │
│           ├── llm_openai / llm_anthropic              │
│           ├── memory_internal                           │
│           ├── tool_butterbase  ← provisions backend     │
│           ├── db_neo4j + llm   ← graph context (Neo4j) │
│           └── tool_daytona / tool_http_request          │
│  Deploy: RocketRide Cloud → shareable endpoint           │
└─────────────────────────────────────────────────────────┘
```

### Why this stack wins

1. **Butterbase** — backend + frontend deploy in hours; qualifies for **$200 prize**
2. **RocketRide** — "thoughtful agent" is literally what pipelines do; Cloud = **live demo URL**
3. **Neo4j** — knowledge graph for agent memory, tasks, relationships = on-theme + partner credit

---

## 8-Hour Sprint Plan (Suggested)

| Block | Time | Focus |
|-------|------|-------|
| **0. Kickoff** | 0:00–0:30 | Read problem statement, pick idea, assign roles |
| **1. Scaffold** | 0:30–1:30 | Butterbase `init_app` + schema + RocketRide `.pipe` skeleton |
| **2. Core agent** | 1:30–3:30 | Agent instructions, tools, one happy-path workflow |
| **3. UI** | 3:30–4:30 | Minimal frontend on Butterbase deploy |
| **4. Deploy** | 4:30–5:00 | RocketRide Cloud + verify live URLs (HTTP GET) |
| **5. Demo prep** | 5:00–5:30 | GitHub repo, README, submission via MCP, rehearse 2-min pitch |

**Rule:** Deploy early. Fix on production. Never demo localhost.

---

## Idea Angles (Theme: Thoughtful Agents for Productivity)

Pick one narrow workflow and nail it:

| Idea | Agent does | Neo4j angle |
|------|------------|-------------|
| **Meeting → tasks** | Ingest notes, extract action items, assign priorities | Task graph: Person → OWNS → Task → BLOCKS → Task |
| **Research assistant** | Multi-step web + doc research, synthesize report | Entity graph: Topic → RELATED_TO → Source |
| **Inbox / ticket triage** | Classify, route, draft replies | User → SUBMITTED → Ticket → SIMILAR_TO → Ticket |
| **Personal knowledge base** | Capture, link, retrieve context for Q&A | GraphRAG over notes + Butterbase RAG |
| **Team standup bot** | Collect updates, surface blockers, summarize | Team → MEMBER → Person → BLOCKED_BY → Issue |
| **Learning planner** | Break goal into steps, track progress | Goal → REQUIRES → Skill → PREREQ → Skill |

**Winning formula:** One clear user, one painful workflow, live demo in <2 minutes.

---

## Your Prep Workspace

```
HackwithBay/
├── reference/
│   ├── hackwithbay-3.0-prep.md      ← this file
│   ├── butterbase-reference.md      ← MCP tools, schema, deploy
│   ├── neo4j-reference.md           ← GraphRAG, Cypher, Aura
│   ├── rocketride-reference.md      ← .pipe, nodes, SDK, Cloud
│   └── rocketride-server/           ← cloned repo + examples/
├── .rocketride/
│   ├── services-catalog.json        ← 100+ pipeline nodes
│   └── schema/*.json                ← node config schemas
```

### Starter pipelines to copy

| File | Use |
|------|-----|
| `reference/rocketride-server/examples/butterbase-agent.pipe` | Agent + Butterbase MCP |
| `reference/rocketride-server/examples/agent-workflow.pipe` | Multi-agent orchestration |
| `reference/rocketride-server/examples/rag-pipeline.pipe` | Document Q&A |
| `reference/rocketride-server/pipelines/git_agent_example.pipe` | Tool-using agent |

---

## Setup Quick Reference

### Butterbase MCP (Cursor)

Already configured at `~/.cursor/mcp.json`. Restart Cursor if needed.

First agent call tonight:

```
butterbase_docs({ topic: "overview" })
init_app({ name: "hackwithbay-app" })
```

Enable **Developer Mode** on the app before create/modify operations.

### RocketRide

1. Install VS Code extension: search "RocketRide"
2. Connection Manager → Local or **RocketRide Cloud**
3. Cloud promo from Discord → deploy pipeline to live URL
4. Or self-host: `./builder build` → `./dist/server/engine ai/eaas.py`

### Neo4j

1. Free AuraDB: https://console.neo4j.io/
2. Use `neo4j+s://` URI in `db_neo4j` node or Neo4j MCP in Cursor
3. See `reference/neo4j-reference.md`

---

## Demo Day Checklist

- [ ] **Live URL** loads in incognito (not localhost)
- [ ] **GitHub repo** public with README (setup + architecture diagram)
- [ ] **2-minute pitch**: problem → agent approach → live demo → partners used
- [ ] Butterbase **app_id** ready for submission
- [ ] Submit via `prep_and_submit_hackathon_entry` MCP tool
- [ ] Mention: Butterbase (backend), RocketRide (agent pipeline), Neo4j (if used)

---

## Key Links

| Resource | URL |
|----------|-----|
| AWS venue registration | http://events.builder.aws.com/d/hdz47p |
| Butterbase dashboard | https://dashboard.butterbase.ai |
| Butterbase billing (promo) | https://dashboard.butterbase.ai/billing |
| RocketRide Cloud | https://cloud.rocketride.ai/ |
| RocketRide Discord | https://discord.com/invite/PMXrtenMsY |
| RocketRide setup docs | (from event: partner link in Luma) |
| RSVP / credits form | https://forms.gle/5YDmGqucsNuRTD8D9 |
| Neo4j Aura | https://console.neo4j.io/ |
| Daytona | https://www.daytona.io/ |

---

## What Organizers Emphasize

1. **Deploy, don't localhost** — RocketRide Cloud + Butterbase frontend deploy
2. **Submit via Butterbase MCP** — not email or Google Form
3. **Best Butterbase use wins $200** — real schema, auth, functions, deployed app
4. **Thoughtful agents** — memory, tools, multi-step reasoning — not a single prompt
5. **Arrive on time** with ID and AWS portal registration complete

---

*Good luck tomorrow. Build narrow, deploy early, demo live. 🚀*
