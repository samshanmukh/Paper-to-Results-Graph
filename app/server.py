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

from fastapi import FastAPI, HTTPException
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


@app.post("/api/run/{method_id}")
def run_method(method_id: str, backend: str = "auto"):
    if backend not in ("auto", "local", "daytona"):
        raise HTTPException(400, "backend must be auto|local|daytona")
    try:
        record = execute(method_id, backend)
    except NotImplementedError as e:
        raise HTTPException(404, str(e))
    curate(record)
    try:  # best-effort mirror to Butterbase run history
        from app.butterbase import sync_run
        sync_run(record)
    except Exception as e:
        record["butterbase_sync_error"] = str(e)
    return record


class Ask(BaseModel):
    question: str


@app.post("/api/ask")
async def ask(body: Ask):
    from rocketride import RocketRideClient
    from rocketride.schema import Question

    client = RocketRideClient()
    await client.connect()
    try:
        result = await client.use(filepath=PIPE)
        q = Question()
        q.addQuestion(body.question)
        response = await client.chat(token=result["token"], question=q)
        answers = response.get("answers", [])
        if not answers:
            for key, lane in response.get("result_types", {}).items():
                if lane == "answers":
                    answers = response.get(key, [])
                    break
        return {"answer": answers[0] if answers else "(no answer from agent)"}
    finally:
        await client.disconnect()
