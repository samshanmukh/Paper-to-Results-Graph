# Neo4j Reference

> **Neo4j** is the leading **graph database** platform. It stores data as **nodes** and **relationships** (a property graph), queried with **Cypher**, and powers knowledge graphs, GraphRAG, fraud detection, recommendations, and AI agent memory.
>
> **Use cases hub:** https://neo4j.com/use-cases/  
> **Docs:** https://neo4j.com/docs/ · **GraphAcademy:** https://graphacademy.neo4j.com/ · **Aura console:** https://console.neo4j.io/

---

## Table of Contents

1. [What Is Neo4j?](#what-is-neo4j)
2. [Property Graph Model](#property-graph-model)
3. [Why Graphs Beat JOINs](#why-graphs-beat-joins)
4. [Product Platform](#product-platform)
5. [Cypher Query Language](#cypher-query-language)
6. [Business Use Cases](#business-use-cases)
7. [Industry Use Cases](#industry-use-cases)
8. [Technical Use Cases](#technical-use-cases)
9. [GraphRAG & Generative AI](#graphrag--generative-ai)
10. [AI Systems & Agent Memory](#ai-systems--agent-memory)
11. [Model Context Protocol (MCP)](#model-context-protocol-mcp)
12. [Graph Data Science (GDS)](#graph-data-science-gds)
13. [Deployment Options](#deployment-options)
14. [SDKs & Drivers](#sdks--drivers)
15. [Framework Integrations](#framework-integrations)
16. [RocketRide Integration (`db_neo4j`)](#rocketride-integration-db_neo4j)
17. [Getting Started](#getting-started)
18. [Quick Reference Card](#quick-reference-card)

---

## What Is Neo4j?

Neo4j is a **native graph database** — data and relationships are stored and traversed directly, not reconstructed with JOINs. It is used to:

- Transform disconnected data into **knowledge graphs**
- Ground LLMs and agents with **connected context** (GraphRAG)
- Detect **fraud rings** and hidden patterns
- Power **real-time recommendations** and customer 360 views
- Model **networks**, supply chains, and IAM hierarchies

> *"Transform data into knowledge to build reliable AI, detect fraud, optimize CX, boost supply chain resilience, and more."* — [Neo4j Use Cases](https://neo4j.com/use-cases/)

**1,700+ organizations** use Neo4j. Languages in the ecosystem: Cypher (primary), openCypher, GQL (ISO standard), Gremlin plugin.

---

## Property Graph Model

| Element | Description | Example |
|---------|-------------|---------|
| **Node** | An entity (record) with labels and properties | `(:Person {name: "Alice", age: 30})` |
| **Relationship** | Directed connection between two nodes, with type and properties | `[:KNOWS {since: 2020}]` |
| **Label** | Category/tag on a node (0..n labels) | `Person`, `Movie` |
| **Property** | Key-value on node or relationship | `name`, `title`, `rating` |

### Conceptual example

```
(Alice:Person)-[:ACTED_IN {role: "Neo"}]->(TheMatrix:Movie)
(Alice:Person)-[:KNOWS]->(Bob:Person)
(Bob:Person)-[:DIRECTED]->(TheMatrix:Movie)
```

### Knowledge graph

A **knowledge graph** is a design pattern: entities + semantic relationships stored as a graph. Neo4j is the most common implementation for production knowledge graphs.

Benefits ([Knowledge Graphs use case](https://neo4j.com/use-cases/knowledge-graph/)):

- **Easy to model** — mirrors real-world relationships
- **Flexible schema** — add labels/properties without migrations
- **Fast traversal** — index-free adjacency (up to ~1000× faster than JOIN-heavy SQL for deep hops)
- **Expressive queries** — Cypher path patterns in few lines

---

## Why Graphs Beat JOINs

| Relational DB | Neo4j |
|---------------|-------|
| Relationships implied by foreign keys | Relationships are **first-class citizens** |
| Deep hops = many JOINs, slow | Traversal follows pointers — cost ~ O(path length) |
| Schema changes need migrations | Add nodes/relationships/properties on the fly |
| Context scattered across tables | Connected context in one query |

**Index-free adjacency:** each node stores direct references to its relationships and neighbors, so graph traversal does not require index lookups at each hop.

---

## Product Platform

### Fully managed (Aura)

| Product | Purpose |
|---------|---------|
| **[AuraDB](https://neo4j.com/product/auradb/)** | Fully managed graph database. 99.95% SLA, auto upgrades, multi-cloud (AWS/Azure/GCP). Free tier available. |
| **[Virtual Graph](https://neo4j.com/product/virtual-graph/)** | Create/query a knowledge graph on top of existing data without full migration |
| **[Aura Graph Analytics](https://neo4j.com/product/aura-graph-analytics/)** | Run graph algorithms at scale on any data, any cloud |
| **[Aura Agent](https://neo4j.com/product/aura-agent/)** | Build and deploy context-aware agents fast |

### Self-managed

| Product | Purpose |
|---------|---------|
| **[Graph Database](https://neo4j.com/product/neo4j-graph-database/)** | On-prem or cloud self-hosted Neo4j |
| **[Graph Data Science (GDS)](https://neo4j.com/product/graph-data-science/)** | 65+ algorithms: PageRank, community detection, pathfinding, embeddings |
| **[Enterprise Studio](https://neo4j.com/product/enterprise-studio/)** | Query, explore, visualize |
| **[Fleet Manager](https://neo4j.com/product/fleet-manager/)** | Manage multiple Neo4j deployments |

### AI capabilities

| Capability | Purpose |
|------------|---------|
| **[Knowledge layer](https://neo4j.com/product/knowledge-layer/)** | Unified context layer for AI |
| **[AI Systems](https://neo4j.com/use-cases/ai-systems/)** | Ground agents with knowledge graphs |
| **[GraphRAG](https://neo4j.com/generativeai/)** | Knowledge graph + vector search for GenAI |

### AuraDB pricing (indicative)

| Tier | Price | Use |
|------|-------|-----|
| Free | $0 | Learn and explore |
| Professional | ~$65/GB/mo (min 1GB) | Production apps |
| Business Critical | ~$146/GB/mo (min 2GB) | Enterprise scale |

---

## Cypher Query Language

Neo4j's declarative query language. **Cypher 25** is the active version (Cypher 5 frozen as of Neo4j 2025.06).

**Manual:** https://neo4j.com/docs/cypher-manual/current/introduction/

### Read patterns

```cypher
// Find people who acted in a movie
MATCH (p:Person)-[:ACTED_IN]->(m:Movie {title: 'The Matrix'})
RETURN p.name, m.title

// Multi-hop: friends of friends
MATCH (me:Person {name: 'Alice'})-[:KNOWS*1..2]-(friend:Person)
RETURN DISTINCT friend.name

// Optional match (left join style)
MATCH (p:Person)
OPTIONAL MATCH (p)-[:ACTED_IN]->(m:Movie)
RETURN p.name, collect(m.title) AS movies
```

### Write patterns

```cypher
// Create
CREATE (a:Person {name: 'Alice'})-[:KNOWS]->(b:Person {name: 'Bob'})

// Merge (create if not exists)
MERGE (p:Person {email: 'alice@example.com'})
ON CREATE SET p.created = datetime()
ON MATCH SET p.lastSeen = datetime()

// Update
MATCH (p:Person {name: 'Alice'})
SET p.age = 31

// Delete
MATCH (p:Person {name: 'Bob'})
DETACH DELETE p
```

### Useful clauses

| Clause | Purpose |
|--------|---------|
| `MATCH` | Find patterns |
| `WHERE` | Filter |
| `RETURN` | Project results |
| `WITH` | Pipeline / aggregate between steps |
| `ORDER BY`, `SKIP`, `LIMIT` | Sort and paginate |
| `CREATE`, `MERGE`, `SET`, `DELETE` | Mutations |
| `EXPLAIN` / `PROFILE` | Query plan / performance analysis |

### Vector search (GenAI)

Neo4j supports **native vector indexes** for semantic similarity alongside graph structure:

```cypher
CALL db.index.vector.queryNodes('movie_embeddings', 5, $queryVector)
YIELD node, score
RETURN node.title, score
```

Combine vector hits with graph expansion for **GraphRAG** retrieval.

---

## Business Use Cases

From [Neo4j Use Cases](https://neo4j.com/use-cases/):

| Use Case | What Neo4j Does | Learn More |
|----------|-------------------|------------|
| **AI Systems** | Model and manage context for production-grade AI; ground agents with knowledge graphs | [ai-systems](https://neo4j.com/use-cases/ai-systems/) |
| **Generative AI / GraphRAG** | Unify vector search, knowledge graph, and data science for accurate, explainable GenAI | [generativeai](https://neo4j.com/generativeai/) |
| **Fraud Detection** | Detect money laundering, credit fraud, claims fraud via connected pattern analysis | [fraud-detection](https://neo4j.com/use-cases/fraud-detection/) |
| **Supply Chain** | Real-time visibility, resilience, planning across suppliers and logistics | [supply-chain](https://neo4j.com/use-cases/supply-chain/) |
| **Real-Time Recommendations** | Recommendations from purchase history, similar users, item groups | [real-time-recommendations](https://neo4j.com/use-cases/real-time-recommendations/) |
| **Customer Experience** | Customer 360, personalization, next-best-action | [customer-experience](https://neo4j.com/use-cases/customer-experience/) |
| **Identity & Access Management** | Users, assets, relationships, authorizations as a graph | [identity-access-management](https://neo4j.com/use-cases/identity-access-management/) |
| **Privacy, Risk & Compliance** | Data lineage, access paths, compliance monitoring | [privacy-risk-compliance](https://neo4j.com/use-cases/privacy-risk-compliance/) |
| **Network & IT Operations** | Dependencies, topology, impact analysis | [network-and-it-operations](https://neo4j.com/use-cases/network-and-it-operations/) |

---

## Industry Use Cases

| Industry | Applications |
|----------|--------------|
| **[Financial Services](https://neo4j.com/use-cases/financial-services/)** | Risk, compliance, fraud, AML |
| **[US Federal Government](https://neo4j.com/use-cases/government/)** | Citizen services, mission analytics |
| **[Healthcare & Life Sciences](https://neo4j.com/use-cases/life-sciences/)** | Drug discovery, patient journeys, affiliations |
| **[Retail](https://neo4j.com/use-cases/retail/)** | Personalization, recommendations, supply chain |
| **[Telecommunications](https://neo4j.com/use-cases/telecom/)** | Network management, customer satisfaction |

---

## Technical Use Cases

| Pattern | Description | Link |
|---------|-------------|------|
| **Knowledge Graphs** | Connect and map data for integration, querying, analysis | [knowledge-graph](https://neo4j.com/use-cases/knowledge-graph/) |
| **Pattern Matching** | Shapes, trends, recurring relationships in raw/semi-structured data | [pattern-matching](https://neo4j.com/use-cases/pattern-matching/) |
| **Digital Twin** | Model real-world systems for visibility and simulation | [Transport for London example](https://neo4j.com/customer-stories/transport-for-london/) |
| **Metadata Management** | Data about data — discovery, lineage, governance | [master-data-management](https://neo4j.com/use-cases/master-data-management/) |

---

## GraphRAG & Generative AI

**GraphRAG** = Retrieval-Augmented Generation enhanced with a **knowledge graph**. Instead of flat vector chunks alone, you retrieve **connected subgraphs** for richer context and explainability.

Source: [Neo4j for GenAI](https://neo4j.com/generativeai/)

### Why GraphRAG over vector-only RAG

| Vector-only RAG | GraphRAG |
|-----------------|----------|
| Similar chunks, no structure | Entities + relationships preserved |
| Hard to explain provenance | Traceable paths from answer to sources |
| Misses multi-hop facts | Multi-hop traversal in one retrieval |
| Context window filled with noise | Higher-relevance subgraph context |

### GraphRAG capabilities

1. **Knowledge graph construction** — extract entities/relations from unstructured text (LLM Graph Builder)
2. **Native vector search** — semantic similarity on nodes
3. **Graph analytics** — 65+ GDS algorithms for enrichment
4. **Framework integrations** — LangChain, LlamaIndex, Hugging Face, etc.
5. **Multi-model LLM support** — OpenAI, Anthropic, Gemini, Bedrock, Ollama, etc.

### Typical GraphRAG pipeline

```
Unstructured docs → Entity/relation extraction → Knowledge graph in Neo4j
                                                        ↓
User question → Vector search (entry nodes) → Graph expansion (hops) → Context → LLM answer
```

### Tools & packages

| Tool | Purpose |
|------|---------|
| **LLM Graph Builder** | Turn documents into knowledge graphs |
| **GraphRAG Python package** | Build retrievers and GraphRAG workflows |
| **Neo4j + LangChain** | GraphCypherQAChain, graph retrieval queries |
| **Neo4j + LlamaIndex** | Property graph index, knowledge graph retrievers |

---

## AI Systems & Agent Memory

From [AI Systems use case](https://neo4j.com/use-cases/ai-systems/):

### Core benefits

- **Boost accuracy** — organize knowledge for reliable retrieval and memory recall
- **Contextual AI** — multi-step retrieval across connected data
- **Explainability** — model data for reasoning traces; subgraph-level access control
- **Future-proof** — enrich context as data and requirements evolve

### Agentic patterns

| Pattern | Description |
|---------|-------------|
| **Agentic GraphRAG** | Agents traverse knowledge graph for multi-hop context |
| **Expert domain knowledge** | NL → Cypher → graph results → LLM summary |
| **Semantic knowledge layer** | Cross-domain context: what data means and how it connects |
| **Enterprise search** | Unify SaaS, legal, security knowledge for agents |
| **Long-term agent memory** | Persist entities, observations, relationships across sessions |

> *"Agentic AI without a knowledge graph is like a self-driving car with no GPS map."* — Merck Group

**Agent memory labs:** https://neo4j.com/labs/agent-memory/

---

## Model Context Protocol (MCP)

Neo4j has extensive MCP support for Cursor, Claude Desktop, VS Code, Windsurf, and agent frameworks.

Docs: [Neo4j MCP Integrations](https://neo4j.com/developer/genai-ecosystem/model-context-protocol-mcp/)

### Official MCP Server

Works with Aura, self-managed, Docker, Desktop, Sandbox.

| Capability | Tools |
|------------|-------|
| Schema | Get graph schema |
| Cypher | Read and write Cypher |
| GDS | Graph algorithms (with GDS plugin) |
| Transport | stdio and HTTP (custom SSL) |

### Neo4j Labs MCP Servers

| Server | Tools / Purpose |
|--------|-----------------|
| **mcp-neo4j-cypher** | `get-neo4j-schema`, `read-neo4j-cypher`, `write-neo4j-cypher` |
| **mcp-neo4j-memory** | Entity/observation/relationship memory graph: `create_entities`, `search_nodes`, `read_graph`, etc. |
| **mcp-neo4j-aura-manager** | `list_instances`, `create_instance`, `pause_instance`, `resume_instance`, tenant management |
| **mcp-neo4j-data-modeling** | Validate models, Mermaid viz, Arrows import/export, example models (fraud, supply chain, customer 360, etc.) |
| **mcp-neo4j-gds** | GDS algorithms as agent tools (PageRank, shortest path, community detection, etc.) |
| **mcp-sandbox** | Create/manage Neo4j Sandboxes, run Cypher, backup to Aura |

### Cursor / IDE setup (example)

```json
{
  "mcpServers": {
    "neo4j-cypher": {
      "command": "npx",
      "args": ["-y", "@neo4j/mcp-neo4j-cypher"],
      "env": {
        "NEO4J_URI": "neo4j+s://your-instance.databases.neo4j.io",
        "NEO4J_USERNAME": "neo4j",
        "NEO4J_PASSWORD": "your-password"
      }
    }
  }
}
```

### Google MCP Toolbox

Parameterized Cypher queries as tools for LangChain, LlamaIndex, Google ADK.  
Article: [Build AI Agents With Google's MCP Toolbox and Neo4j](https://neo4j.com/blog/)

---

## Graph Data Science (GDS)

65+ production-ready algorithms:

| Category | Examples |
|----------|----------|
| **Centrality** | PageRank, Betweenness, Degree, Eigenvector |
| **Community** | Louvain, Label Propagation, Weakly Connected Components |
| **Pathfinding** | Dijkstra, A*, Yen's k-shortest paths, BFS, DFS |
| **Similarity** | Node similarity, KNN, filtered KNN |
| **Embeddings** | Node2Vec, FastRP for ML features |

Use cases: fraud rings, influence analysis, recommendations, supply chain risk, link prediction.

---

## Deployment Options

| Option | Best for |
|--------|----------|
| **AuraDB Free** | Learning, prototypes |
| **AuraDB Professional** | Production SaaS |
| **AuraDB Business Critical** | Enterprise SLA, compliance |
| **Docker** | Local dev: `docker run neo4j` |
| **Neo4j Desktop** | Local GUI + multiple DBs |
| **Self-managed** | On-prem, air-gapped, custom infra |
| **Neo4j Sandbox** | Free temporary cloud instances (also via MCP) |

### Connection URIs

| URI scheme | Meaning |
|------------|---------|
| `neo4j://localhost:7687` | Bolt, local |
| `bolt://host:7687` | Bolt protocol |
| `neo4j+s://xxx.databases.neo4j.io` | Aura with TLS |
| `bolt+s://...` | TLS Bolt |

Default ports: **7687** (Bolt), **7474** (HTTP browser).

---

## SDKs & Drivers

Official drivers: **Python**, **JavaScript**, **Java**, **.NET**, **Go**

```python
from neo4j import GraphDatabase

driver = GraphDatabase.driver(
    "neo4j+s://xxx.databases.neo4j.io",
    auth=("neo4j", "password")
)

with driver.session(database="neo4j") as session:
    result = session.run(
        "MATCH (p:Person)-[:KNOWS]->(f:Person) WHERE p.name = $name RETURN f.name",
        name="Alice"
    )
    for record in result:
        print(record["f.name"])
```

**Downloads:** https://neo4j.com/download/  
**Deployment Center:** https://neo4j.com/deployment-center/

---

## Framework Integrations

| Framework | Integration |
|-----------|-------------|
| **LangChain** | `Neo4jGraph`, `GraphCypherQAChain`, MCP adapters |
| **LlamaIndex** | Property graph index, MCP tool spec |
| **CrewAI** | MCP servers as crew tools |
| **Pydantic AI** | MCP client + server support |
| **Google ADK** | MCP Toolbox + Neo4j |
| **Semantic Kernel** | MCP tools → SK functions |
| **AWS Bedrock** | GraphRAG on Bedrock |
| **Google Vertex AI** | Native GraphRAG integration |
| **Databricks / Snowflake** | Graph analytics connectors |

Cloud partners: [AWS](https://neo4j.com/partners/aws/), [Azure](https://neo4j.com/partners/microsoft/), [Google Cloud](https://neo4j.com/partners/google/), [Snowflake](https://neo4j.com/neo4j-graph-analytics-snowflake/)

---

## RocketRide Integration (`db_neo4j`)

RocketRide includes a **`db_neo4j`** node for natural-language graph Q&A in pipelines.

Docs: `reference/rocketride-server/packages/docs/content-static/integrations/neo4j.md`

### What it does

- Connects via **Bolt** (official `neo4j` Python driver)
- Takes **natural-language questions** on `questions` lane
- Uses connected **LLM** to generate **Cypher**
- Returns results on `table`, `text`, `answers` lanes
- Exposes agent tools: `get_data`, `get_schema`, `get_cypher`

### When to use

| Scenario | Why Neo4j node |
|----------|----------------|
| **Graph RAG** | Retrieve connected entities/relationships vs flat chunks |
| **Knowledge-graph Q&A** | Plain English → Cypher → graph results |
| **Entity linking** | Multi-hop traversal without hand-written queries |
| **Agent memory** | Agent queries graph during reasoning loop |

### Configuration

```json
{
  "id": "graph_1",
  "provider": "neo4jdb",
  "config": {
    "uri": "neo4j+s://your-instance.databases.neo4j.io",
    "auth_method": "userpass",
    "user": "neo4j",
    "password": "${NEO4J_PASSWORD}",
    "database": "neo4j",
    "db_description": "Movie graph: People, Movies, ACTED_IN, DIRECTED relationships."
  }
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `uri` | `neo4j://localhost:7687` | Bolt URI (`neo4j+s://` for Aura TLS) |
| `auth_method` | `userpass` | `userpass` or `token` (Aura bearer) |
| `database` | `neo4j` | Database name |
| `db_description` | `""` | Domain description — improves Cypher accuracy |
| `max_attempts` | `5` | LLM retries after `EXPLAIN` failures |
| `allow_execute` | `false` | Allow raw Cypher via `QuestionType.EXECUTE` |

### Safety (read-only by default)

Blocked clauses: `CREATE`, `MERGE`, `DELETE`, `SET`, `DROP`, `LOAD CSV`, mutating `apoc`, etc.

Allowed: `MATCH`, `OPTIONAL MATCH`, `WITH`, `WHERE`, `RETURN`, `ORDER BY`, `SKIP`, `LIMIT` (must end with `LIMIT`).

- 30-second query timeout
- `EXPLAIN` validation loop with LLM retry
- Schema reflected at pipeline start from live DB

### Env vars (RocketRide)

```bash
NEO4J_URI=neo4j+s://your-instance.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=
```

### Example pipeline pattern

```
chat (source) → agent_langchain ← tool_butterbase (backend)
                     ↑
              db_neo4j + llm_openai (graph RAG context)
```

Pair with **vector DB** (Qdrant, Pinecone) for hybrid retrieval: vectors find entry points, graph expands context.

---

## Getting Started

### 1. Free AuraDB instance

1. Sign up at https://console.neo4j.io/
2. Create free AuraDB instance
3. Save connection URI, username, password
4. Open **Neo4j Browser** or connect via driver

### 2. Learn Cypher (GraphAcademy — free)

| Course | Duration |
|--------|----------|
| [Neo4j Fundamentals](https://graphacademy.neo4j.com/) | 1 hour |
| [Cypher Fundamentals](https://graphacademy.neo4j.com/) | 1 hour |
| [Graph Data Modeling](https://graphacademy.neo4j.com/) | 2 hours |
| [Build a Neo4j-backed chatbot (Python)](https://graphacademy.neo4j.com/) | 2 hours |
| [Introduction to vector indexes](https://graphacademy.neo4j.com/) | 2 hours |
| [Building knowledge graphs with LLMs](https://graphacademy.neo4j.com/) | 2 hours |

### 3. Build GraphRAG

1. Load sample data or use **LLM Graph Builder**
2. Create vector index on text embeddings
3. Wire LangChain/LlamaIndex graph retriever
4. Connect to your LLM of choice

### 4. Add to Cursor

Install **mcp-neo4j-cypher** or official Neo4j MCP server (see [MCP section](#model-context-protocol-mcp)).

### 5. Use in RocketRide

Add `db_neo4j` node + `llm_openai` (or other LLM) on control channel; connect `questions` lane from chat or agent.

---

## Quick Reference Card

```
Model:     Nodes + Relationships + Properties (property graph)
Query:     Cypher — MATCH (a)-[r]->(b) RETURN a, r, b
Connect:   Bolt port 7687 — neo4j+s:// for Aura TLS
Managed:   AuraDB Free → console.neo4j.io
AI:        GraphRAG = vectors + knowledge graph expansion
Agents:    MCP servers for Cypher, memory, GDS, data modeling
RocketRide: db_neo4j node — NL → Cypher → graph results (read-only)
```

### Cypher one-liners

```cypher
MATCH (n) RETURN count(n)                    // node count
CALL db.labels()                             // all labels
CALL db.relationshipTypes()                  // all rel types
MATCH (n:Person) RETURN n LIMIT 25           // sample nodes
MATCH p=(:Person)-[*1..3]-(:Person) RETURN p LIMIT 10  // paths
```

### Neo4j vs relational vs vector DB

| Need | Use |
|------|-----|
| Fixed schemas, aggregates, reports | PostgreSQL / SQL |
| Semantic similarity on text | Vector DB (Qdrant, Pinecone) |
| Relationships, paths, fraud rings, KG, GraphRAG | **Neo4j** |
| Best of both | Hybrid: vectors find seeds → graph expands context |

---

## Key Links

| Resource | URL |
|----------|-----|
| Use cases hub | https://neo4j.com/use-cases/ |
| AI systems | https://neo4j.com/use-cases/ai-systems/ |
| GraphRAG / GenAI | https://neo4j.com/generativeai/ |
| Knowledge graphs | https://neo4j.com/use-cases/knowledge-graph/ |
| MCP integrations | https://neo4j.com/developer/genai-ecosystem/model-context-protocol-mcp/ |
| Developer hub | https://neo4j.com/developer/ |
| AI + graph | https://neo4j.com/developer/ai/ |
| Cypher manual | https://neo4j.com/docs/cypher-manual/current/ |
| GraphAcademy | https://graphacademy.neo4j.com/ |
| AuraDB | https://neo4j.com/product/auradb/ |
| Pricing | https://neo4j.com/pricing/ |
| Community | https://community.neo4j.com/ |
| Discord | https://discord.com/invite/neo4j |

---

*Compiled for HackwithBay reference. Pair with `reference/butterbase-reference.md` (backend) and `reference/rocketride-server/` (pipeline engine).*
