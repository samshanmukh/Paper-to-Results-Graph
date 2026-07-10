"""FastAPI backend for the Verigraph demo UI.

Endpoints:
  GET  /             -> landing page
  GET  /demo         -> live demo page
  GET  /api/workspace     -> workspace status (papers, empty, runs)
  POST /api/workspace/new -> blank workspace (wipe graph + papers)
  POST /api/workspace/load-demo -> restore bundled demo papers
  DELETE /api/workspace/papers/{paper_id} -> remove one paper from workspace
  POST /api/reset         -> clear runs on current workspace (pristine evidence)
  GET  /api/evidence -> evidence table rows
  POST /api/run/{method_id}?backend=auto|local|daytona -> closed loop:
       codegen -> execute -> curate -> return run record
  POST /api/ask      -> question through the RocketRide agent pipeline (+ Cognee recall when enabled)
  POST /api/memory/recall -> semantic search over indexed papers and runs (Cognee)

Run: .venv/bin/uvicorn app.server:app --port 8787
"""

import asyncio
import os
import re
import secrets
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.curator import curate
from app.db import DATABASE, OUR_LABELS, get_driver
from app.queries import run_query
from app.runner import execute

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")
PIPE = os.path.join(ROOT, "pipelines", "verigraph.pipe")
PIPE_LEGACY = os.path.join(ROOT, "pipelines", "paper2result.pipe")


def _pipe_path() -> str:
    """Prefer verigraph.pipe; accept legacy symlink/name for running engines."""
    if os.path.isfile(PIPE):
        return PIPE
    if os.path.isfile(PIPE_LEGACY):
        return PIPE_LEGACY
    return PIPE

app = FastAPI(title="Verigraph")


@app.get("/")
def landing():
    return FileResponse(os.path.join(STATIC, "landing.html"))


@app.get("/demo")
def demo():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/admin")
@app.get("/admin/")
def visitor_admin():
    return FileResponse(os.path.join(STATIC, "admin.html"))


class DemoRegistration(BaseModel):
    email: str
    timezone: str = ""


def _request_location(request: Request) -> dict:
    forwarded = request.headers.get("x-forwarded-for", "")
    return {
        "ip": request.headers.get("cf-connecting-ip")
        or forwarded.split(",")[0].strip()
        or (request.client.host if request.client else ""),
        "country": request.headers.get("cf-ipcountry")
        or request.headers.get("x-vercel-ip-country", ""),
        "region": request.headers.get("x-vercel-ip-country-region", ""),
        "city": request.headers.get("x-vercel-ip-city", ""),
    }


@app.post("/api/register")
def register_demo_visitor(body: DemoRegistration, request: Request):
    email = body.email.strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(400, "Enter a valid email address.")
    try:
        from app.butterbase import register_visitor

        visitor = register_visitor(email, _request_location(request), body.timezone)
        return {"ok": True, "visitor_id": visitor["id"], "email": visitor["email"]}
    except Exception as e:
        raise HTTPException(503, f"visitor tracking unavailable: {e}")


@app.get("/api/admin/users")
def admin_demo_users(request: Request):
    expected = os.environ.get("ADMIN_TRACKING_KEY", "")
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not expected:
        raise HTTPException(503, "ADMIN_TRACKING_KEY is not configured.")
    if len(expected) < 16 or not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, "Unauthorized")
    try:
        from app.butterbase import list_visitors

        return {"users": list_visitors(), "generated_at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        raise HTTPException(503, f"visitor tracking unavailable: {e}")


@app.middleware("http")
async def track_demo_tools(request: Request, call_next):
    response = await call_next(request)
    visitor_id = request.query_params.get("visitor", "")
    if (
        response.status_code < 400
        and request.method == "POST"
        and request.url.path.startswith("/api/")
        and request.url.path != "/api/register"
        and re.fullmatch(r"[0-9a-f-]{36}", visitor_id, re.IGNORECASE)
    ):
        try:
            from app.butterbase import record_visitor_tool

            tool = request.url.path.removeprefix("/api/").replace("/", ":")[:80]
            await asyncio.to_thread(record_visitor_tool, visitor_id, tool)
        except Exception:
            pass
    return response


@app.post("/api/reset")
def reset_demo():
    from app.demo_reset import reset_demo_state

    try:
        return reset_demo_state()
    except Exception as e:
        raise HTTPException(500, f"reset failed: {e}")


@app.get("/api/workspace")
def workspace_info():
    from app.workspace import workspace_status

    try:
        return workspace_status()
    except Exception as e:
        raise HTTPException(500, f"workspace status failed: {e}")


@app.post("/api/workspace/new")
def workspace_new():
    from app.workspace import new_workspace

    try:
        return new_workspace()
    except Exception as e:
        raise HTTPException(500, f"new workspace failed: {e}")


@app.post("/api/workspace/load-demo")
def workspace_load_demo():
    from app.workspace import load_demo_workspace

    try:
        return load_demo_workspace()
    except Exception as e:
        raise HTTPException(500, f"load demo failed: {e}")


@app.delete("/api/workspace/papers/{paper_id}")
def workspace_remove_paper(paper_id: str):
    from app.workspace import remove_paper

    try:
        return remove_paper(paper_id)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"remove paper failed: {e}")


@app.get("/api/graph")
def graph():
    with get_driver() as driver:
        node_recs, _, _ = driver.execute_query(
            """
            MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels)
            RETURN elementId(n) AS eid, labels(n)[0] AS label,
                   coalesce(n.id, n.name) AS key,
                   coalesce(n.title, n.name, n.text, n.id) AS caption,
                   properties(n) AS props
            """,
            labels=OUR_LABELS, database_=DATABASE,
        )
        edge_recs, _, _ = driver.execute_query(
            """
            MATCH (a)-[r]->(b)
            WHERE any(l IN labels(a) WHERE l IN $labels)
              AND any(l IN labels(b) WHERE l IN $labels)
            RETURN elementId(a) AS src, elementId(b) AS dst, type(r) AS rel
            """,
            labels=OUR_LABELS, database_=DATABASE,
        )
    return {
        "nodes": [dict(r) for r in node_recs],
        "edges": [dict(r) for r in edge_recs],
    }


@app.get("/api/evidence")
def evidence():
    with get_driver() as driver:
        return run_query(driver, "evidence")


class RunBody(BaseModel):
    params: dict = {}


@app.post("/api/run/{method_id}")
def run_method(method_id: str, backend: str = "auto", body: RunBody | None = None):
    if backend not in ("auto", "local", "daytona"):
        raise HTTPException(400, "backend must be auto|local|daytona")
    try:
        record = execute(method_id, backend, params=(body.params if body else None))
    except NotImplementedError as e:
        raise HTTPException(404, str(e))
    curate(record)
    try:  # best-effort mirror to Butterbase run history
        from app.butterbase import sync_run
        sync_run(record)
    except Exception as e:
        record["butterbase_sync_error"] = str(e)
    return record


def _ingest_paper_text(text: str) -> dict:
    """Paper text -> LLM extraction -> files + graph + Butterbase mirror."""
    import json as _json

    from app.extract import EXTRACTED_DIR, PAPERS_DIR, extract_live_text
    from app.graph import load_claim_relations, load_paper

    if len(text.strip()) < 200:
        raise HTTPException(400, "paper text too short — need at least the abstract and method section")
    data = extract_live_text(text)
    pid = data["paper"]["id"]

    with open(os.path.join(PAPERS_DIR, f"{pid}.txt"), "w") as f:
        f.write(text)
    with open(os.path.join(EXTRACTED_DIR, f"{pid}.json"), "w") as f:
        _json.dump(data, f, indent=2)

    with get_driver() as driver, driver.session(database=DATABASE) as session:
        session.execute_write(load_paper, data)
        session.execute_write(load_claim_relations, data)

    try:  # best-effort mirror
        from app.butterbase import upsert
        upsert("papers", {"id": pid, "title": data["paper"]["title"],
                          "year": data["paper"]["year"],
                          "arxiv": data["paper"].get("arxiv"),
                          "topic": data["paper"].get("topic"),
                          "extraction": data})
    except Exception:
        pass

    try:
        from app.cognee_memory import remember_paper_sync

        remember_paper_sync(pid, data["paper"]["title"], text, data)
    except Exception:
        pass

    return {"paper_id": pid, "title": data["paper"]["title"],
            "claims": len(data["claims"]), "methods": len(data["methods"]),
            "method_ids": [m["id"] for m in data["methods"]],
            "relations": len(data.get("claim_relations", []))}


def _pdf_to_text(blob: bytes) -> str:
    import io

    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(blob))
    # first ~12 pages carry abstract + method; keeps extraction prompt lean
    return "\n".join(page.extract_text() or "" for page in reader.pages[:12])


class Upload(BaseModel):
    text: str


@app.post("/api/upload")
def upload(body: Upload):
    return _ingest_paper_text(body.text)


@app.post("/api/upload-file")
async def upload_file(file: UploadFile):
    blob = await file.read()
    if len(blob) > 25_000_000:
        raise HTTPException(400, "file too large (25 MB max)")
    if (file.filename or "").lower().endswith(".pdf") or blob[:4] == b"%PDF":
        text = _pdf_to_text(blob)
    else:
        text = blob.decode(errors="replace")
    return _ingest_paper_text(text)


class ArxivUpload(BaseModel):
    url: str


@app.post("/api/upload-arxiv")
def upload_arxiv(body: ArxivUpload):
    from app.arxiv import fetch_arxiv_text, parse_arxiv_id

    try:
        parse_arxiv_id(body.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        text = fetch_arxiv_text(body.url)
    except Exception as e:
        raise HTTPException(502, f"arXiv fetch failed: {e}")
    return _ingest_paper_text(text)


CONDUCT_PIPE = os.path.join(ROOT, "pipelines", "paper-orchestrator.pipe")


class Conduct(BaseModel):
    message: str


def _method_exists(method_id: str) -> bool:
    with get_driver() as driver:
        recs, _, _ = driver.execute_query(
            "MATCH (m:Method {id: $id}) RETURN count(m) AS c",
            id=method_id, database_=DATABASE)
        return recs[0]["c"] == 1


@app.post("/api/conduct")
async def conduct(body: Conduct):
    """Conductor workflow: investigator → execute → reporter.

    Uses RocketRide specialist pipes when the engine is up; falls back to
    graph-grounded Python audit + canonical execute spine otherwise."""
    from app.grounded_qa import agent_unavailable, answer_brief, answer_conduct

    if os.environ.get("GROUNDED_AGENTS", "").lower() in ("1", "true", "yes"):
        return answer_conduct()

    steps = []

    # STEP 1 — Investigator (checks V1/V3/V4, retry once)
    parsed = _parse_p2r_block(await _agent_chat(INVESTIGATE_PIPE, INVESTIGATE_PROMPT))
    if agent_unavailable(parsed.get("prose") or ""):
        return await asyncio.to_thread(answer_conduct)
    payload = parsed.get("payload") or {}
    method_id = payload.get("recommended_method_id")
    if not (parsed["header_present"] and method_id and _method_exists(method_id)):
        parsed = _parse_p2r_block(await _agent_chat(
            INVESTIGATE_PIPE,
            "Your prior response failed check V3_method_recommended: the "
            "recommended_method_id must be an EXACT Method.id from a Neo4j query "
            "(e.g. wilson2017-m1). Re-run the audit and return the required "
            "---P2R--- block."))
        payload = parsed.get("payload") or {}
        method_id = payload.get("recommended_method_id")
    if not (method_id and _method_exists(method_id)):
        # conservative fallback: deterministic pick — untested method
        with get_driver() as driver:
            recs, _, _ = driver.execute_query(
                """
                MATCH (m:Method) WHERE NOT (:Run)-[:IMPLEMENTS]->(m)
                RETURN m.id AS id LIMIT 1
                """, database_=DATABASE)
            method_id = recs[0]["id"] if recs else "wilson2017-m1"
        steps.append(f"[investigator] ✗ id check failed twice — fell back to "
                     f"deterministic pick: {method_id}")
    else:
        steps.append(f"[investigator] ✓ {len(payload.get('conflicts') or [])} conflicts, "
                     f"{len(payload.get('untested_claims') or [])} untested — "
                     f"recommended {method_id}")

    # STEP 2 — Executor: canonical spine (codegen -> sandbox -> curate)
    record = await asyncio.to_thread(execute, method_id, "auto")
    await asyncio.to_thread(curate, record)
    try:
        from app.butterbase import sync_run
        sync_run(record)
    except Exception:
        pass
    checks = (record.get("result") or {}).get("claim_checks", [])
    if record.get("error"):
        steps.append(f"[executor] ✗ {record['run_id']} failed: {record['error']} "
                     f"(failure curated as evidence)")
    else:
        verdicts = ", ".join(f"{c['verdict']} {c['claim_id']}" for c in checks)
        steps.append(f"[executor] ✓ {record['run_id']} [{record['backend']}] "
                     f"{record['duration_s']}s — {verdicts}")

    # STEP 3 — Reporter (checks R1 + real ids, retry once)
    rparsed = _parse_p2r_block(await _agent_chat(BRIEF_PIPE, BRIEF_PROMPT))
    if agent_unavailable(rparsed.get("prose") or ""):
        rparsed = answer_brief()
        rparsed["prose"] = rparsed["answer"]
    rpayload = rparsed.get("payload") or {}
    covered = rpayload.get("run_ids_covered") or []
    if not (rparsed["header_present"] and covered):
        rparsed = _parse_p2r_block(await _agent_chat(
            BRIEF_PIPE,
            "Your prior response failed check R1_runs_covered. Query the Run "
            "nodes again and return the required ---P2R--- block with "
            "run_ids_covered copied exactly from the query."))
        rpayload = rparsed.get("payload") or {}
        covered = rpayload.get("run_ids_covered") or []
    steps.append(f"[reporter] {'✓' if covered else '✗'} brief over "
                 f"{len(covered)} runs — {rpayload.get('headline', '')[:90]}")

    answer = "\n".join(steps) + "\n\n" + (rparsed.get("prose") or "")
    return {"answer": answer, "steps": steps, "run_id": record.get("run_id"),
            "method_id": method_id, "investigator": payload, "reporter": rpayload}


class Ask(BaseModel):
    question: str


# One shared RocketRide connection; one cached task token per .pipe file.
# The engine refuses a second `use` of a running pipeline ("Pipeline is
# already running"), so we start each once and attach on later requests.
_rr: dict = {"client": None, "tokens": {}}


async def _pipeline_token(pipe_path: str):
    import json as _json

    from rocketride import RocketRideClient

    if _rr["client"] is None:
        client = RocketRideClient()
        await client.connect()
        _rr["client"] = client
    client = _rr["client"]
    if pipe_path not in _rr["tokens"]:
        try:
            result = await client.use(filepath=pipe_path)
            _rr["tokens"][pipe_path] = result["token"]
        except RuntimeError as e:
            if "already running" not in str(e).lower():
                raise
            with open(pipe_path) as f:
                project_id = _json.load(f)["project_id"]
            token = await client.get_task_token(project_id, "chat_1")
            if not token:
                raise
            _rr["tokens"][pipe_path] = token
    return client, _rr["tokens"][pipe_path]


def _core_counts() -> tuple:
    with get_driver() as driver:
        recs, _, _ = driver.execute_query(
            "RETURN count { MATCH (p:Paper) } AS p, count { MATCH (r:Run) } AS r",
            database_=DATABASE)
        return recs[0]["p"], recs[0]["r"]


async def _agent_chat(pipe_path: str, question_text: str) -> str:
    from rocketride.schema import Question

    # LLM-generated Cypher has (rarely) emitted write clauses despite the
    # read-only contract; snapshot core counts so we can self-heal from disk.
    try:
        before = _core_counts()
    except Exception:
        before = None
    try:
        client, token = await _pipeline_token(pipe_path)
        q = Question()
        q.addQuestion(question_text)
        response = await client.chat(token=token, question=q)
    except Exception as e:
        # drop the cached session so the next request reconnects fresh
        stale = _rr.pop("client", None)
        _rr.update({"client": None, "tokens": {}})
        if stale is not None:
            try:
                await stale.disconnect()
            except Exception:
                pass
        return f"(agent unavailable: {type(e).__name__}: {e})"

    answers = response.get("answers", [])
    if not answers:
        for key, lane in response.get("result_types", {}).items():
            if lane == "answers":
                answers = response.get(key, [])
                break
    answer = answers[0] if answers else "(no answer from agent)"

    if before is not None:
        try:
            after = _core_counts()
            if after[0] < before[0] or after[1] < before[1]:
                from app.restore import restore_all
                restored = restore_all()
                answer += (f"\n\n[graph guard] a generated query removed nodes "
                           f"(papers {before[0]}→{after[0]}, runs {before[1]}→{after[1]}); "
                           f"auto-restored {restored['papers']} papers + "
                           f"{restored['runs']} runs from disk.")
        except Exception:
            pass
    return answer


def _parse_p2r_block(answer: str) -> dict:
    """Parse the ---P2R--- machine header sub-agents are contracted to emit."""
    import json as _json
    import re

    out = {"agent": None, "status": None, "payload": None,
           "prose": answer, "header_present": False}
    m = re.search(r"---P2R---(.*?)---END---", answer, re.DOTALL)
    if not m:
        return out
    out["header_present"] = True
    block = m.group(1)
    out["prose"] = answer[m.end():].strip()
    if am := re.search(r"agent:\s*(\w+)", block):
        out["agent"] = am.group(1)
    if sm := re.search(r"status:\s*(\w+)", block):
        out["status"] = sm.group(1)
    if pm := re.search(r"payload:\s*(\{.*)", block, re.DOTALL):
        try:
            raw = pm.group(1).strip().splitlines()[0]
            out["payload"] = _json.loads(raw)
        except Exception:
            try:  # payload may span lines up to ---END---
                out["payload"] = _json.loads(pm.group(1).strip())
            except Exception:
                out["payload"] = None
    return out


@app.post("/api/ask")
async def ask(body: Ask):
    from app.cognee_memory import is_enabled, recall_context
    from app.grounded_qa import agent_unavailable, answer_ask

    question = body.question.strip()
    memory = ""
    if is_enabled():
        memory = await recall_context(question)
    prompt = question
    if memory:
        prompt = (
            "You have semantic memory from indexed papers and experiment runs (Cognee). "
            "Use it together with Neo4j graph queries. Cite exact metrics when present.\n\n"
            f"Semantic memory:\n{memory}\n\nQuestion: {question}"
        )
    answer = await _agent_chat(_pipe_path(), prompt)
    grounded = agent_unavailable(answer)
    if grounded:
        answer = answer_ask(question)
    if memory and not answer.startswith("[semantic") and not answer.startswith("**Cognee"):
        answer = f"[semantic memory ✓]\n\n{answer}"
    try:
        from app.cognee_memory import log_session_qa_sync

        log_session_qa_sync(question, answer)
    except Exception:
        pass
    return {"answer": answer, "memory_used": bool(memory), "grounded": grounded}


class MemoryRecall(BaseModel):
    query: str
    paper_id: str | None = None
    top_k: int = 5


@app.post("/api/memory/recall")
async def memory_recall(body: MemoryRecall):
    from app.cognee_memory import is_enabled, recall

    if not is_enabled():
        raise HTTPException(
            501,
            "Cognee memory is disabled. Set COGNEE_ENABLED=true and configure "
            "ROCKETRIDE_GATEWAY_* (local) or COGNEE_CLOUD + COGNEE_SERVICE_URL + COGNEE_API_KEY.",
        )
    snippets = await recall(body.query, paper_id=body.paper_id, top_k=body.top_k)
    return {"query": body.query, "results": snippets, "count": len(snippets)}


INVESTIGATE_PIPE = os.path.join(ROOT, "pipelines", "paper-investigate.pipe")
BRIEF_PIPE = os.path.join(ROOT, "pipelines", "paper-brief.pipe")
EXECUTE_PIPE = os.path.join(ROOT, "pipelines", "paper-execute.pipe")


class ExecuteBody(BaseModel):
    method_id: str
    params: dict = {}


@app.post("/api/execute")
async def execute_agent(body: ExecuteBody):
    """Executor sub-agent path: agent drives POST /api/run via its HTTP tool."""
    prompt = (f"Run method {body.method_id} now"
              + (f" with parameter overrides {body.params}" if body.params else "")
              + ". Use your HTTP tool against the canonical API and report exactly.")
    answer = await _agent_chat(EXECUTE_PIPE, prompt)
    parsed = _parse_p2r_block(answer)
    return {"answer": parsed["prose"] or answer, **{k: parsed[k] for k in
            ("agent", "status", "payload", "header_present")}}

INVESTIGATE_PROMPT = ("Audit the graph: list all cross-paper CONTRADICTS conflicts and "
                      "untested claims, then recommend exactly one method to run next.")
BRIEF_PROMPT = ("Generate the evidence brief covering all experiment runs: validated, "
                "refuted, untested claims, with exact metrics.")


@app.post("/api/investigate")
async def investigate():
    from app.grounded_qa import agent_unavailable, answer_investigate

    answer = await _agent_chat(INVESTIGATE_PIPE, INVESTIGATE_PROMPT)
    parsed = _parse_p2r_block(answer)
    if agent_unavailable(parsed.get("prose") or answer):
        return answer_investigate()
    return {"answer": parsed["prose"] or answer, **{k: parsed[k] for k in
            ("agent", "status", "payload", "header_present")}}


@app.post("/api/brief")
async def brief():
    from app.grounded_qa import agent_unavailable, answer_brief

    with get_driver() as driver:
        recs, _, _ = driver.execute_query(
            "MATCH (r:Run) RETURN count(r) AS c", database_=DATABASE)
        if recs[0]["c"] == 0:
            raise HTTPException(409, "no runs in the graph yet — run a method first")
    answer = await _agent_chat(BRIEF_PIPE, BRIEF_PROMPT)
    parsed = _parse_p2r_block(answer)
    if agent_unavailable(parsed.get("prose") or answer):
        return answer_brief()
    return {"answer": parsed["prose"] or answer, **{k: parsed[k] for k in
            ("agent", "status", "payload", "header_present")}}
