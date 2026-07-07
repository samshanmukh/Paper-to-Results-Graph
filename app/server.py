"""FastAPI backend for the Paper-to-Results demo UI.

Endpoints:
  GET  /             -> static demo page
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
PIPE = os.path.join(ROOT, "pipelines", "paper2result.pipe")

app = FastAPI(title="Paper-to-Results Graph")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


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
    import re
    import urllib.request

    m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?", body.url.strip())
    if not m:
        raise HTTPException(400, "couldn't find an arXiv id in that link (expected e.g. arxiv.org/abs/1907.08610)")
    arxiv_id = m.group(1)
    req = urllib.request.Request(
        f"https://export.arxiv.org/pdf/{arxiv_id}",
        headers={"User-Agent": "paper2result/1.0 (hackathon demo)"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            blob = resp.read()
    except Exception as e:
        raise HTTPException(502, f"arXiv fetch failed: {e}")
    return _ingest_paper_text(_pdf_to_text(blob))


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


async def _agent_chat(pipe_path: str, question_text: str) -> str:
    from rocketride.schema import Question

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
    return answers[0] if answers else "(no answer from agent)"


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
    return {"answer": await _agent_chat(PIPE, body.question)}


INVESTIGATE_PIPE = os.path.join(ROOT, "pipelines", "paper-investigate.pipe")
BRIEF_PIPE = os.path.join(ROOT, "pipelines", "paper-brief.pipe")

INVESTIGATE_PROMPT = ("Audit the graph: list all cross-paper CONTRADICTS conflicts and "
                      "untested claims, then recommend exactly one method to run next.")
BRIEF_PROMPT = ("Generate the evidence brief covering all experiment runs: validated, "
                "refuted, untested claims, with exact metrics.")


@app.post("/api/investigate")
async def investigate():
    answer = await _agent_chat(INVESTIGATE_PIPE, INVESTIGATE_PROMPT)
    parsed = _parse_p2r_block(answer)
    return {"answer": parsed["prose"] or answer, **{k: parsed[k] for k in
            ("agent", "status", "payload", "header_present")}}


@app.post("/api/brief")
async def brief():
    with get_driver() as driver:
        recs, _, _ = driver.execute_query(
            "MATCH (r:Run) RETURN count(r) AS c", database_=DATABASE)
        if recs[0]["c"] == 0:
            raise HTTPException(409, "no runs in the graph yet — run a method first")
    answer = await _agent_chat(BRIEF_PIPE, BRIEF_PROMPT)
    parsed = _parse_p2r_block(answer)
    return {"answer": parsed["prose"] or answer, **{k: parsed[k] for k in
            ("agent", "status", "payload", "header_present")}}
