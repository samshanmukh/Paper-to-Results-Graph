# Butterbase Reference

> AI-optimized Backend-as-a-Service (BaaS). Describe what you need in natural language; an AI assistant provisions apps, schema, auth, storage, functions, frontends, AI/RAG, and more via MCP tools or REST APIs.
>
> **Docs:** https://docs.butterbase.ai · **Dashboard:** https://dashboard.butterbase.ai  
> **Source:** Compiled from official `butterbase_docs` MCP (July 2026)

---

## Table of Contents

1. [What Butterbase Provides](#what-butterbase-provides)
2. [URLs & Authentication](#urls--authentication)
3. [How to Connect](#how-to-connect)
4. [Typical Workflow](#typical-workflow)
5. [MCP Tools (Complete Catalog)](#mcp-tools-complete-catalog)
6. [REST API Summary](#rest-api-summary)
7. [End-User Authentication](#end-user-authentication)
8. [Declarative Schema (DSL)](#declarative-schema-dsl)
9. [Row-Level Security (RLS)](#row-level-security-rls)
10. [File Storage](#file-storage)
11. [Serverless Functions](#serverless-functions)
12. [Frontend Deployment](#frontend-deployment)
13. [AI Model Gateway](#ai-model-gateway)
14. [RAG (Retrieval-Augmented Generation)](#rag-retrieval-augmented-generation)
15. [Agents](#agents)
16. [Realtime WebSockets](#realtime-websockets)
17. [KV Store](#kv-store)
18. [Durable Objects & Edge SSR](#durable-objects--edge-ssr)
19. [Integrations (Gmail, Slack, etc.)](#integrations-gmail-slack-etc)
20. [Substrate (Cross-App Agent Memory)](#substrate-cross-app-agent-memory)
21. [Billing & Monetization](#billing--monetization)
22. [Organizations & People](#organizations--people)
23. [SDK & CLI](#sdk--cli)
24. [RocketRide Integration](#rocketride-integration)
25. [Hackathon Submission](#hackathon-submission)
26. [Important Gotchas](#important-gotchas)

---

## What Butterbase Provides

| Capability | Description |
|------------|-------------|
| **Apps** | Isolated backend per project: own database, `app_id`, API base URL |
| **Regions** | Choose where data/compute live; move apps between regions |
| **Declarative schema** | JSON schema DSL; platform diffs and applies migrations safely |
| **Auto data API** | Full CRUD over HTTP — filter, sort, paginate — no route codegen |
| **End-user auth** | Email/password, OAuth (Google, GitHub, Discord, etc.), JWT |
| **RLS** | Per-user row isolation with one-shot or custom policies |
| **File storage** | Presigned upload/download URLs; store `objectId` in DB |
| **Serverless functions** | TypeScript/JS; HTTP or cron triggers; DB + env access |
| **Frontend hosting** | Deploy React/Vite, Next.js static, or plain HTML to live URL |
| **AI gateway** | OpenAI-compatible chat, embeddings, video generation |
| **RAG** | Upload docs → auto chunk/embed/index → natural language query |
| **Agents** | Define graph-spec agents with rate limits and visibility |
| **Realtime** | WebSocket change notifications on tables |
| **KV store** | Redis-like key-value with exposure rules |
| **Integrations** | Gmail, Slack, GitHub, 1000+ via Composio-style toolkits |
| **Substrate** | Per-user cross-app memory, decisions, action ledger for agents |
| **Billing** | Free/Pro/Enterprise plans; Stripe Connect for your end users |
| **Audit logs** | Auth, admin, and function invocation events |

**Developer Mode** must be enabled on an app before agents can create or modify resources.

---

## URLs & Authentication

| Purpose | URL Pattern |
|---------|-------------|
| **MCP endpoint** | `https://api.butterbase.ai/mcp` |
| **Data API** | `https://api.butterbase.ai/v1/{app_id}/...` |
| **Auth API** | `https://api.butterbase.ai/auth/{app_id}/...` *(not under `/v1`)* |
| **Functions** | `https://api.butterbase.ai/v1/{app_id}/fn/{function_name}` |
| **Realtime WS** | `wss://api.butterbase.ai/v1/{app_id}/realtime` |
| **AI chat** | `POST /v1/{app_id}/chat/completions` |
| **AI embeddings** | `POST /v1/{app_id}/embeddings` |
| **Health** | `GET /ping` |

### API key types

| Prefix | Use |
|--------|-----|
| `bb_sk_...` | Platform/service key — full MCP + Control API access |
| `bb_sub_...` | Substrate-scoped key (substrate routes only) |

Generate keys: dashboard → API Keys, or `manage_auth_config` action `generate_service_key`.

### Cursor MCP config (`~/.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "butterbase": {
      "url": "https://api.butterbase.ai/mcp",
      "headers": {
        "Authorization": "Bearer bb_sk_YOUR_KEY"
      }
    }
  }
}
```

Restart Cursor after editing. If tools return 401, authenticate via Settings → MCP.

---

## How to Connect

| Method | Best for |
|--------|----------|
| **MCP in Cursor/Claude** | Agent-driven backend provisioning during development |
| **REST API** | Frontend apps, custom integrations |
| **`@butterbase/sdk`** | TypeScript browser/Node/Deno clients |
| **`@butterbase/cli`** | Terminal workflows, substrate keys |
| **RocketRide `tool_butterbase`** | Pipeline agents calling Butterbase as tools |
| **Serverless `ctx.*`** | In-function DB, storage, substrate, idempotency |

---

## Typical Workflow

1. `init_app` — create app, note `app_id` and API URL
2. `manage_schema` — define tables (`dry_run` first, then `apply`)
3. `manage_rls` — enable user isolation on user-owned tables
4. `manage_oauth` / auth config — configure end-user sign-in
5. `manage_app` action `update_cors` — allow your frontend origin
6. `deploy_function` — custom backend logic (optional)
7. `manage_ai` / RAG — AI features (optional)
8. `create_frontend_deployment` + `manage_frontend` — ship frontend
9. `select_rows` / `insert_row` / `seed_database` — populate data

---

## MCP Tools (Complete Catalog)

### App & Platform

| Tool | Actions / Purpose |
|------|-------------------|
| **`init_app`** | Create app. Args: `name`, optional `region`, `organization_id` |
| **`list_regions`** | List available regions for create/move |
| **`manage_app`** | `list`, `delete`, `get_config`, `update_cors`, `move`, `move_status`, `teardown_source_replica` |
| **`manage_migrations`** | `get_active`, `abort`, `reverse`, `list` — in-flight region migrations |
| **`manage_auth_config`** | `update_jwt`, `generate_service_key` |
| **`manage_api_keys`** | Create/list/revoke platform API keys |
| **`manage_organizations`** | Org management |
| **`manage_people`** | Team/people management |
| **`manage_repo`** | Repo linking (monorepo workflows) |
| **`butterbase_docs`** | Read docs. Topics: `overview`, `mcp`, `rest`, `auth`, `storage`, `functions`, `frontend`, `ai`, `rag`, `billing`, `platform`, `regions`, `schema`, `sdk`, `cli`, `realtime`, `integrations`, `substrate`, `all` |
| **`submit_suggestion`** | Feedback / bug reports to Butterbase team |
| **`mcp_auth`** | Authenticate MCP session in clients that require it |

### Schema & Data

| Tool | Actions / Purpose |
|------|-------------------|
| **`manage_schema`** | `get`, `apply`, `dry_run`, `list_migrations` |
| **`select_rows`** | Query with filter/sort/pagination. Optional `as_role` (`anon`/`user`) + `as_user` for RLS testing |
| **`insert_row`** | Insert one row. Same RLS simulation options |
| **`seed_database`** | Bulk insert multiple rows |

### Auth & Security

| Tool | Actions / Purpose |
|------|-------------------|
| **`manage_oauth`** | `configure`, `get`, `update`, `delete` — built-in: google, github, discord, facebook, linkedin, microsoft, apple, x |
| **`manage_rls`** | `enable`, `create_policy`, `update_policy`, `create_user_isolation`, `list`, `delete` |
| **`manage_auth_users`** | Manage end-user accounts |
| **`query_audit_logs`** | Filter auth/admin/function events by category, type, actor, date range |

### Storage

| Tool | Actions |
|------|---------|
| **`manage_storage`** | `upload_url`, `download_url`, `list`, `delete`, `update_config` |

**Remember:** persist `objectId` in your DB, not `objectKey` or presigned URLs (they expire).

### Serverless Functions

| Tool | Actions / Purpose |
|------|-------------------|
| **`deploy_function`** | Deploy TS/JS. Triggers: `http`, `cron`, `websocket` |
| **`manage_function`** | `list`, `delete`, `update_env`, `get_logs` |
| **`invoke_function`** | Test-invoke with method, body, headers |

### Frontend

| Tool | Actions / Purpose |
|------|-------------------|
| **`create_frontend_deployment`** | Get deployment ID + upload URL. Frameworks: `react-vite`, `nextjs-static`, `static`, `other` |
| **`manage_frontend`** | `start_deployment`, `list_deployments`, `set_env` |
| **`manage_edge_ssr`** | Edge SSR deployment management |

### AI & RAG

| Tool | Actions / Purpose |
|------|-------------------|
| **`manage_ai`** | `chat`, `embed`, `list_models`, `get_config`, `update_config`, `get_usage`, `submit_video`, `poll_video`, meeting actions (`start_meeting`, `get_meeting`, `list_meetings`, `stop_meeting`, `estimate_meeting`, `configure_meetings_webhook`, `usage_meetings`) |
| **`manage_rag_content`** | Collections: `create_collection`, `list_collections`, `get_collection`, `delete_collection`. Docs: `ingest`, `list`, `status`, `delete` |
| **`rag_query`** | Semantic search + optional `synthesize` answer |
| **`manage_agents`** | `list`, `get`, `create`, `update`, `delete`, `validate` — graph-spec agents |

### Realtime, KV, Durable Objects

| Tool | Actions |
|------|---------|
| **`manage_realtime`** | `configure` (enable on tables), `get` |
| **`manage_kv`** | Config: `list_rules`, `expose`, `unexpose`, `stats`, `scan`, `flush`. Data: `get`, `set`, `del`, `incr`, `decr`, `setnx`, `setex`, `cas`, `exists`, `ttl`, `expire`, `mget`, `mset` |
| **`manage_durable_objects`** | Stateful edge objects (class deploy, instances) |

### Integrations & Substrate

| Tool | Actions |
|------|---------|
| **`manage_integrations`** | `configure`, `list`, `disable`, `list_available`, connect/execute flows |
| **`manage_substrate`** | Cross-app user memory, entities, decisions, action ledger, attention rules |

### Billing

| Tool | Actions |
|------|---------|
| **`manage_billing`** | Plan usage, Stripe Connect onboarding, subscription/product management |

### Hackathon

| Tool | Actions |
|------|---------|
| **`prep_and_submit_hackathon_entry`** | `prep` — resolve hackathon + get field schema. `submit` — send entry. Pass `app_id` for bonus scoring |

---

## REST API Summary

### App management

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/apps` | List apps |
| POST | `/init` | Create app `{"name": "my-app"}` |
| DELETE | `/apps/{app_id}` | Delete app permanently |

### Schema

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/v1/{app_id}/schema` | Read current schema |
| POST | `/v1/{app_id}/schema/apply` | Apply schema (`dry_run: true` to preview) |
| GET | `/v1/{app_id}/migrations` | Migration history |

### Data (auto-generated per table)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/v1/{app_id}/{table}` | List rows (filter/sort/paginate) |
| GET | `/v1/{app_id}/{table}/{id}` | Get row |
| POST | `/v1/{app_id}/{table}` | Create row |
| PATCH | `/v1/{app_id}/{table}/{id}` | Update row |
| DELETE | `/v1/{app_id}/{table}/{id}` | Delete row |

### Filter operators

`column=operator.value` — operators: `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `like`, `ilike`, `in`, `is`

Example: `?status=eq.published&created_at=gte.2026-01-01&order=created_at.desc&limit=20`

---

## End-User Authentication

Base: `/auth/{app_id}/`

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/signup` | POST | Register (5 req/15min) |
| `/login` | POST | Email/password → access + refresh tokens |
| `/refresh` | POST | Rotate tokens |
| `/logout` | POST | Revoke refresh tokens (Bearer required) |
| `/verify-email` | POST | 6-digit code (24h expiry) |
| `/forgot-password` | POST | Request reset code |
| `/reset-password` | POST | Reset with code (1h expiry) |
| `/oauth/{provider}` | GET | Start OAuth flow |
| `/oauth/{provider}/callback` | GET/POST | OAuth callback |
| `/me` | GET | Current user profile |
| `/.well-known/jwks.json` | GET | JWT verification keys |

**Password rules:** 8+ chars, uppercase, lowercase, number, special char.

**Token defaults:** access 1h, refresh 7d (configurable via `manage_auth_config`).

**Frontend pattern:** After login, send `Authorization: Bearer {access_token}` on data API calls. RLS applies automatically.

---

## Declarative Schema (DSL)

```json
{
  "schema": {
    "tables": {
      "posts": {
        "columns": {
          "id": { "type": "uuid", "primary": true, "default": "gen_random_uuid()" },
          "title": { "type": "text", "nullable": false },
          "body": { "type": "text" },
          "user_id": { "type": "uuid", "nullable": false },
          "created_at": { "type": "timestamptz", "default": "now()" }
        },
        "indexes": {
          "idx_posts_user": { "columns": ["user_id"] }
        }
      }
    }
  },
  "dry_run": true,
  "name": "add posts table"
}
```

### Column types

| Category | Types |
|----------|-------|
| Text | `text`, `varchar`, `varchar(N)`, `char`, `char(N)` |
| Numbers | `integer`, `bigint`, `smallint`, `real`, `float4`, `float8`, `decimal`, `numeric` |
| Other | `boolean`, `uuid`, `timestamp`, `timestamptz`, `date`, `json`, `jsonb`, `bytea`, `vector(N)`, arrays |

### Column properties

`primary`, `nullable`, `unique`, `default`, `references: { table, column }`

### Destructive changes

- Drop columns: `"_dropColumns": ["col1"]` inside table
- Drop table: `"_drop": true` on table entry
- Always `dry_run: true` first
- Max 50 tables per schema apply

---

## Row-Level Security (RLS)

| MCP action | Purpose |
|------------|---------|
| `enable` | Turn on RLS for a table |
| `create_user_isolation` | One-shot: user can only see/edit own rows via `user_id` column |
| `create_policy` / `update_policy` | Custom SELECT/INSERT/UPDATE/DELETE policies |
| `list` / `delete` | Inspect or remove policies |

**Roles:**
- `butterbase_user` — end-user JWT; RLS enforced
- `butterbase_service` — platform API key; RLS bypassed
- `butterbase_anon` — unauthenticated; anon policies only

Test RLS via `select_rows`/`insert_row` with `as_role` + `as_user`.

---

## File Storage

**Flow:** `upload_url` → PUT file to presigned URL → store `objectId` in DB → `download_url` when displaying.

| Persist | Don't persist |
|---------|---------------|
| `objectId` (UUID) | `objectKey` (internal path) |
| | Presigned URLs (expire) |

Supported in functions via storage APIs. For images in UI, resolve fresh download URLs in parallel.

---

## Serverless Functions

```typescript
export default async function handler(req: Request, ctx: any): Promise<Response> {
  const body = await req.json();
  // ctx.env.VAR_NAME — function env vars (not Deno.env for app vars)
  // ctx.user.id — when called with end-user JWT
  // ctx.db.query() — database access
  // ctx.db.asUser(userId, fn) — test RLS as user
  // ctx.idempotency.claim(key) — webhook deduplication
  return new Response(JSON.stringify({ ok: true }), {
    headers: { 'Content-Type': 'application/json' }
  });
}
```

| Trigger | Config |
|---------|--------|
| `http` | `{}` — callable at `/v1/{app_id}/fn/{name}` |
| `cron` | `{"schedule": "0 9 * * *"}` |
| `websocket` | `{"event": "chat_message"}` — fires on WS custom events |

Defaults: 30s timeout (max 300s), 128MB memory (64–1024MB).

---

## Frontend Deployment

1. `create_frontend_deployment` → get `deployment_id` + `uploadUrl`
2. Upload zip via `curl -X PUT` (use forward-slash paths inside zip)
3. `manage_frontend` action `start_deployment`
4. Poll until `READY`, then **verify HTTP** — CDN edge may lag minutes

| Framework | Value | Build output |
|-----------|-------|--------------|
| React (Vite) | `react-vite` | `dist/` |
| Next.js static | `nextjs-static` | `out/` |
| Plain HTML | `static` | any static files |

**Do not use** Windows Explorer "Send to → Compressed folder" or PowerShell `Compress-Archive` for deployment zips.

### Frontend API URLs (critical)

```
Data API:  https://api.butterbase.ai/v1/{app_id}
Auth API:  https://api.butterbase.ai/auth/{app_id}   ← NOT under /v1
```

Set `is_published=true` on public-readable rows. Email verification not required to log in after signup.

---

## AI Model Gateway

OpenAI-compatible endpoints:

| Endpoint | Purpose |
|----------|---------|
| `POST /v1/{app_id}/chat/completions` | Chat (supports vision: `image_url`, `video_url` parts) |
| `POST /v1/{app_id}/embeddings` | Vector embeddings |
| `POST /v1/{app_id}/videos/completions` | Async video generation |
| `GET /v1/{app_id}/ai/models` | Model catalog |
| `GET/PUT /v1/{app_id}/ai/config` | Default model, BYOK key, allowed models |

**Example models:** `anthropic/claude-3.5-sonnet`, `openai/gpt-4o`, `openai/text-embedding-3-small`, `meta-llama/llama-3.1-70b-instruct`

BYOK: bring your own provider key, or use platform credits.

---

## RAG (Retrieval-Augmented Generation)

**Collections** — namespaces for documents. Access modes: `private`, `shared`, `custom`.

**Ingest:** PDF, TXT, MD, CSV, HTML, DOCX, XLSX, PPTX. Status: `pending → processing → ready`.

**Query:** `rag_query` with `query`, `top_k`, `threshold`, `filter`, `synthesize: true` for LLM answer.

```
1. manage_rag_content → create_collection
2. manage_storage → upload_url → upload file
3. manage_rag_content → ingest (storage_object_id or raw text)
4. manage_rag_content → status (wait for ready)
5. rag_query → natural language question
```

---

## Agents

`manage_agents` — define agents with `graph_spec`, `default_model`, `visibility` (`private`/`authenticated`/`public`), rate limits, `daily_budget_usd`, `max_concurrent_runs`.

Validate spec before deploy: action `validate`.

---

## Realtime WebSockets

Connect: `wss://api.butterbase.ai/v1/{app_id}/realtime?token={jwt}`

```json
{ "type": "subscribe", "table": "messages", "filter": { "channel_id": "abc" } }
```

Server sends: `connected`, `subscribed`, `change` (INSERT/UPDATE/DELETE), `heartbeat`, `presence_*`, `event_response`.

Enable: `manage_realtime` action `configure` with table list.

RLS enforced on realtime events per subscriber role.

---

## KV Store

Redis-like per-app KV. Expose namespaces to clients with read/write rules (`public`, `authed`, `owner`, `deny`).

Operations: `get`, `set`, `del`, `incr`, `decr`, `setnx`, `setex`, `cas`, `mget`, `mset`, `scan`, `ttl`, `expire`, `flush`.

---

## Durable Objects & Edge SSR

- **`manage_durable_objects`** — stateful edge instances (class-based)
- **`manage_edge_ssr`** — server-side rendering at the edge

---

## Integrations (Gmail, Slack, etc.)

**Flow:** configure toolkit → end user connects via OAuth → execute actions on their behalf.

**Curated:** gmail, google-calendar, slack, google-sheets, notion, github, hubspot, outlook, google-drive, discord.

`manage_integrations`: `configure`, `list`, `disable`, `list_available` (+ connect/execute via REST).

---

## Substrate (Cross-App Agent Memory)

Per-user memory layer shared across apps: entities, decisions, commitments, learnings, action ledger, attention rules.

**Use when:** state should follow the user across apps (OKRs, commitments, CRM contacts).

**Skip when:** app-local state (use regular DB tables).

**Auth:** `bb_sub_*` keys, platform JWT, or `ctx.substrate` in functions.

**Key capabilities:** `propose`, `searchMemory`, `listMemory`, `findEntities`, `listActions`, attention rules, WebSocket stream.

Memory writes auto-execute; side-effecting actions (e.g. `send_email_draft`) require human approval.

---

## Billing & Monetization

### Platform plans

| | Free | Pro | Enterprise |
|---|------|-----|------------|
| Price | $0 | $25/mo | Custom |
| AI credits | $0.10 lifetime | $10/mo + overage | Unlimited |
| DB | 500 MB | 8 GB | Unlimited |
| Storage | 1 GB | 100 GB | Unlimited |
| Functions | 50k/mo | 500k/mo | Unlimited |

### Monetize your app (Stripe Connect)

Separate from your Butterbase subscription — sell to **your end users**:

- Subscription plans: `/v1/{app_id}/billing/plans`, `/billing/subscribe`
- One-time products: `/billing/products`, `/billing/purchase`
- Connect onboarding: `/billing/connect/onboard`

---

## Organizations & People

- **`manage_organizations`** — multi-tenant org management
- **`manage_people`** — team members, roles
- Apps can be created under an `organization_id`

---

## SDK & CLI

### TypeScript SDK (`@butterbase/sdk`)

```bash
npm install @butterbase/sdk
```

```typescript
import { createClient } from '@butterbase/sdk';

const bb = createClient({
  appId: 'app_abc123',
  apiUrl: 'https://api.butterbase.ai',
});

// Data
const { data } = await bb.from('posts').select('*').eq('status', 'published');

// Auth
await bb.auth.signUp({ email, password });
await bb.auth.signIn({ email, password });

// Storage
const { data: upload } = await bb.storage.getUploadUrl({ filename, contentType, size });
const { data: download } = await bb.storage.getDownloadUrl(objectId);

// RAG
await bb.rag.createCollection({ name: 'docs', accessMode: 'shared' });
await bb.rag.ingest('docs', { text: '...' });
const result = await bb.rag.query('docs', { query: '...', synthesize: true });
```

### CLI (`@butterbase/cli`)

```bash
butterbase keys generate --substrate   # substrate-scoped key
```

---

## RocketRide Integration

In RocketRide pipelines, use the **`tool_butterbase`** node:

- Connects to `https://api.butterbase.ai/mcp` via Streamable HTTP
- Exposes tools as `butterbase.<toolName>` to agent nodes
- Requires `bearer` API key in node config
- Example pipeline: `reference/rocketride-server/examples/butterbase-agent.pipe`

**Agent instructions pattern:**
1. Call `butterbase.butterbase_docs` topic `overview` first
2. `init_app` → schema → RLS → auth → functions → frontend
3. Keep DB column names identical in frontend code
4. Use absolute API URLs; confirm frontend deployment `READY` via HTTP

Schema in HackwithBay: `.rocketride/schema/tool_butterbase.json`

---

## Hackathon Submission

Tool: **`prep_and_submit_hackathon_entry`**

| Action | Purpose |
|--------|---------|
| `prep` | Resolve hackathon from `submission_code`; returns `field_schema` |
| `submit` | Send `data` object matching schema. Pass `app_id` for bonus points |

```json
{
  "action": "submit",
  "hackathon_slug": "from-prep-response",
  "app_id": "app_abc123",
  "data": {
    "project_name": "My App",
    "demo_url": "https://my-app.butterbase.app",
    "description": "What it does"
  }
}
```

---

## Important Gotchas

1. **Developer Mode** — must be on for agent create/modify operations
2. **Auth vs Data URLs** — auth is `/auth/{app_id}`, data is `/v1/{app_id}` — don't mix them
3. **Storage** — save `objectId`, not `objectKey` or presigned URLs
4. **Schema field names** — use identical names in DB, API, and frontend (`body` not `content`)
5. **RLS testing** — platform key bypasses RLS; use `as_role`/`as_user` or end-user JWT to test
6. **Function env vars** — use `ctx.env`, not `Deno.env`, for deployed env vars
7. **Frontend zip** — forward slashes only; avoid Windows `Compress-Archive`
8. **Frontend READY** — verify with HTTP GET; CDN propagation takes minutes
9. **Destructive schema** — requires explicit `_drop` / `_dropColumns`; always dry-run first
10. **Substrate keys** — `bb_sub_*` only; regular `bb_sk_*` returns 403 on substrate routes
11. **Video models** — use `/videos/completions`, not `/chat/completions`
12. **Published rows** — set `is_published=true` for anonymous public reads

---

## Quick Reference Card

```
Create app     → init_app({ name: "my-app" })
Schema         → manage_schema({ action: "dry_run" | "apply", schema: {...} })
User isolation → manage_rls({ action: "create_user_isolation", table_name, user_column })
Seed data      → seed_database({ app_id, table, rows: [...] })
Deploy fn      → deploy_function({ app_id, name, code, trigger })
Deploy UI      → create_frontend_deployment → upload zip → manage_frontend start_deployment
Query data     → select_rows({ app_id, table, filters... })
AI chat        → manage_ai({ action: "chat", messages: [...] })
RAG            → manage_rag_content ingest → rag_query
Hackathon      → prep_and_submit_hackathon_entry({ action: "prep" | "submit" })
Read docs      → butterbase_docs({ topic: "overview" })
```
