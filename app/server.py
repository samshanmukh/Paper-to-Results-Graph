"""FastAPI backend for the Verigraph demo UI.

Endpoints:
  GET  /             -> landing page
  GET  /demo         -> live demo page
  GET  /api/graph    -> full project graph (nodes + edges) for visualization
  GET  /api/evidence -> evidence table rows
  POST /api/run/{method_id}?backend=auto|local|daytona -> closed loop:
       codegen -> execute -> curate -> return run record
  POST /api/ask      -> question through the RocketRide agent pipeline

Run: .venv/bin/uvicorn app.server:app --port 8787
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, UploadFile
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


@app.post("/api/reset")
def reset_demo():
    from app.demo_reset import reset_demo_state

    try:
        return reset_demo_state()
    except Exception as e:
        raise HTTPException(500, f"reset failed: {e}")


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


class Ask(BaseModel):
    question: str


# One shared RocketRide connection + pipeline token. The engine refuses a
# second `use` of a running pipeline ("Pipeline is already running"), so we
# start it once and attach to the existing task on subsequent requests.
_rr: dict = {"client": None, "token": None}


async def _pipeline_token():
    import json as _json

    from rocketride import RocketRideClient

    if _rr["client"] is None:
        client = RocketRideClient()
        await client.connect()
        _rr["client"] = client
    client = _rr["client"]
    if _rr["token"] is None:
        pipe = _pipe_path()
        try:
            result = await client.use(filepath=pipe)
            _rr["token"] = result["token"]
        except RuntimeError as e:
            if "already running" not in str(e).lower():
                raise
            with open(pipe) as f:
                project_id = _json.load(f)["project_id"]
            _rr["token"] = await client.get_task_token(project_id, "chat_1")
            if not _rr["token"]:
                raise
    return client, _rr["token"]


@app.post("/api/ask")
async def ask(body: Ask):
    from rocketride.schema import Question

    try:
        client, token = await _pipeline_token()
        q = Question()
        q.addQuestion(body.question)
        response = await client.chat(token=token, question=q)
    except Exception as e:
        # drop the cached session so the next ask reconnects fresh
        stale = _rr.pop("client", None)
        _rr.update({"client": None, "token": None})
        if stale is not None:
            try:
                await stale.disconnect()
            except Exception:
                pass
        return {"answer": f"(agent unavailable: {type(e).__name__}: {e})"}

    answers = response.get("answers", [])
    if not answers:
        for key, lane in response.get("result_types", {}).items():
            if lane == "answers":
                answers = response.get(key, [])
                break
    return {"answer": answers[0] if answers else "(no answer from agent)"}
