"""FastAPI backend for the Verigraph demo UI.

Endpoints:
  GET  /             -> landing page
  GET  /demo         -> live demo page
  GET  /api/workspace     -> workspace status (papers, empty, runs)
  POST /api/workspace/new -> blank active workspace (repository assets stay immutable)
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
from contextlib import asynccontextmanager
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from app.curator import curate
from app.db import DATABASE, GRAPH_NAMESPACE, OUR_LABELS, get_driver
from app.queries import run_query
from app.runner import execute
from app.security import (
    RequestBodyLimitMiddleware,
    authorize_expensive_request,
    enforce_rate_limit,
    reject_oversized_request,
    require_api_key,
)

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

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    try:
        yield
    finally:
        await close_rocketride_client()


app = FastAPI(title="Verigraph", lifespan=_lifespan)
app.add_middleware(RequestBodyLimitMiddleware)


def _concurrency_limit(name: str, default: int) -> int:
    try:
        return max(1, min(32, int(os.environ.get(name, str(default)))))
    except ValueError:
        return default


_execution_slots = asyncio.Semaphore(_concurrency_limit("VERIGRAPH_MAX_CONCURRENT_RUNS", 2))
_ingestion_slots = asyncio.Semaphore(_concurrency_limit("VERIGRAPH_MAX_CONCURRENT_INGESTS", 1))


@app.middleware("http")
async def protect_api(request: Request, call_next):
    """Apply body bounds, coarse rate limits, and baseline response headers."""
    try:
        reject_oversized_request(request)
        if request.url.path.startswith("/api/"):
            enforce_rate_limit(request, "default", 120)
            if (
                request.method in {"POST", "PUT", "PATCH", "DELETE"}
                and request.url.path != "/api/register"
            ):
                require_api_key(request)
    except HTTPException as exc:
        response = JSONResponse(
            {"detail": exc.detail},
            status_code=exc.status_code,
            headers=exc.headers or {},
        )
    else:
        response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "frame-ancestors 'none'; base-uri 'self'; object-src 'none'",
    )
    return response


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


@app.get("/config.js")
def frontend_config():
    return FileResponse(os.path.join(STATIC, "config.js"), media_type="application/javascript")


class DemoRegistration(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    timezone: str = Field(default="", max_length=100)


_VISITOR_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.IGNORECASE,
)


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
    enforce_rate_limit(request, "registration", 10)
    email = body.email.strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(400, "Enter a valid email address.")
    try:
        from app.butterbase import register_visitor

        visitor = register_visitor(email, _request_location(request), body.timezone)
        return {"ok": True, "visitor_id": visitor["id"], "email": visitor["email"]}
    except Exception as e:
        raise HTTPException(503, "visitor tracking unavailable") from e


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
        raise HTTPException(503, "visitor tracking unavailable") from e


@app.middleware("http")
async def track_demo_tools(request: Request, call_next):
    response = await call_next(request)
    visitor_id = request.query_params.get("visitor", "")
    if (
        response.status_code < 400
        and request.method == "POST"
        and request.url.path.startswith("/api/")
        and request.url.path != "/api/register"
        and _VISITOR_ID.fullmatch(visitor_id)
    ):
        try:
            from app.butterbase import record_visitor_tool

            tool = request.url.path.removeprefix("/api/").replace("/", ":")[:80]
            await asyncio.to_thread(record_visitor_tool, visitor_id, tool)
        except Exception:
            pass
    return response


def _workspace_result(result: dict) -> dict:
    if result.get("partial") or not result.get("ok", False):
        sync = result.get("sync") or {}
        raise HTTPException(
            503,
            {
                "message": result.get("message", "workspace recovery is required"),
                "operation_id": sync.get("operation_id"),
                "stage": sync.get("stage") or sync.get("state"),
            },
        )
    return result


def _mirror_workspace(result: dict) -> dict:
    """Publish the active manifest only after the local transition succeeds."""
    mirrors = result.setdefault("mirrors", {})
    try:
        from app.butterbase import sync_workspace

        state = sync_workspace()
        mirrors["butterbase"] = "synchronized"
        result["butterbase_workspace"] = state
    except Exception as exc:
        mirrors["butterbase"] = f"deferred: {type(exc).__name__}"
    return result


@app.post("/api/reset")
def reset_demo(request: Request):
    authorize_expensive_request(request, bucket="workspace", default_limit=10)
    from app.demo_reset import reset_demo_state

    try:
        return _mirror_workspace(_workspace_result(reset_demo_state()))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "reset failed") from e


@app.get("/api/workspace")
def workspace_info():
    from app.workspace import workspace_status

    try:
        return workspace_status()
    except Exception as e:
        raise HTTPException(500, "workspace status failed") from e


@app.get("/api/health")
def health():
    from app.research_tools import load_impl_bundle

    return {
        "ok": True,
        "live_run": bool(os.environ.get("DAYTONA_API_KEY")),
        "impl_methods": len(load_impl_bundle()),
    }


def _heuristic_extraction(text: str, title: str | None = None, arxiv: str | None = None) -> dict:
    """Create a bounded local-only extraction when LLM extraction is unavailable."""
    cleaned = " ".join(str(text or "").split())
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    title = title or next(
        (line for line in lines if 12 < len(line) < 180 and not line.lower().startswith("arxiv:")),
        "Untitled local paper",
    )
    year_match = re.search(r"\b((?:19|20)\d{2})\b", title + " " + cleaned)
    year = int(year_match.group(1)) if year_match else datetime.now().year
    slug = re.sub(r"[^a-z0-9]+", "", title.lower())[:18] or "localpaper"
    paper_id = f"arxiv{str(arxiv).replace('.', '')}"[:32] if arxiv else f"{slug}{year}"[:32]
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if 40 < len(s.strip()) < 320][:3]
    if not sentences:
        sentences = [cleaned[:200]]
    return {
        "paper": {"id": paper_id, "title": title[:240], "authors": ["local"], "year": year, "arxiv": arxiv, "topic": "local-import"},
        "claims": [{"id": f"{paper_id}-c{i + 1}", "text": sentence, "metric": None} for i, sentence in enumerate(sentences)],
        "methods": [{"id": f"{paper_id}-m1", "name": "Toy reproduction experiment", "description": f"Simplified simulation of {title[:80]}.", "runnable_hint": "Build a small synthetic task and report a metric.", "params": [{"name": "n_trials", "default": 200, "description": "synthetic problems"}]}],
        "datasets": [], "cites": [], "claim_relations": [],
    }


@app.post("/api/extract-local-text")
def extract_local_text(body: dict, request: Request):
    authorize_expensive_request(request, bucket="ingest", default_limit=20)
    from app.extract import ensure_methods, extract_live_text

    text = str((body or {}).get("text") or "")
    if len(text.strip()) < 200:
        raise HTTPException(400, "paper text too short — need at least ~200 characters")
    try:
        data, source = extract_live_text(text), "text-llm"
    except Exception:
        data, source = _heuristic_extraction(text, (body or {}).get("title"), (body or {}).get("arxiv")), "text-heuristic"
    return {"extraction": ensure_methods(data), "source": source, "local": True}


@app.post("/api/extract-local-arxiv")
def extract_local_arxiv(body: dict, request: Request):
    authorize_expensive_request(request, bucket="ingest", default_limit=20)
    from app.arxiv import fetch_arxiv_text, parse_arxiv_id
    from app.extract import ensure_methods, extract_live_text

    url = str((body or {}).get("url") or "")
    try:
        arxiv_id, _ = parse_arxiv_id(url)
        text = fetch_arxiv_text(url)
    except Exception as exc:
        raise HTTPException(400, f"arXiv fetch failed: {exc}") from exc
    try:
        data, source = extract_live_text(text), "arxiv-llm"
    except Exception:
        data, source = _heuristic_extraction(text, arxiv=arxiv_id), "arxiv-heuristic"
    data.setdefault("paper", {})["arxiv"] = data["paper"].get("arxiv") or arxiv_id
    return {"extraction": ensure_methods(data), "source": source, "local": True}


@app.post("/api/workspace/new")
def workspace_new(request: Request):
    authorize_expensive_request(request, bucket="workspace", default_limit=10)
    from app.workspace import new_workspace

    try:
        return _mirror_workspace(_workspace_result(new_workspace()))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "new workspace failed") from e


@app.post("/api/workspace/load-demo")
def workspace_load_demo(request: Request):
    authorize_expensive_request(request, bucket="workspace", default_limit=10)
    from app.workspace import load_demo_workspace

    try:
        return _mirror_workspace(_workspace_result(load_demo_workspace()))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "load demo failed") from e


@app.delete("/api/workspace/papers/{paper_id}")
def workspace_remove_paper(paper_id: str, request: Request):
    authorize_expensive_request(request, bucket="workspace", default_limit=10)
    from app.workspace import remove_paper
    from app.validation import require_paper_id

    try:
        paper_id = require_paper_id(paper_id)
        return _mirror_workspace(_workspace_result(remove_paper(paper_id)))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "remove paper failed") from e


@app.get("/api/graph")
def graph():
    with get_driver() as driver:
        node_recs, _, _ = driver.execute_query(
            """
            MATCH (n:Verigraph {verigraph_namespace: $graph_namespace})
            WHERE any(l IN labels(n) WHERE l IN $labels)
            RETURN elementId(n) AS eid,
                   head([l IN labels(n) WHERE l IN $labels]) AS label,
                   coalesce(n.id, n.name) AS key,
                   coalesce(n.title, n.name, n.text, n.id) AS caption,
                   properties(n) AS props
            """,
            labels=OUR_LABELS, graph_namespace=GRAPH_NAMESPACE, database_=DATABASE,
        )
        edge_recs, _, _ = driver.execute_query(
            """
            MATCH (a:Verigraph {verigraph_namespace: $graph_namespace})-[r]->
                  (b:Verigraph {verigraph_namespace: $graph_namespace})
            WHERE any(l IN labels(a) WHERE l IN $labels)
              AND any(l IN labels(b) WHERE l IN $labels)
            RETURN elementId(a) AS src, elementId(b) AS dst, type(r) AS rel
            """,
            labels=OUR_LABELS, graph_namespace=GRAPH_NAMESPACE, database_=DATABASE,
        )
    return {
        "nodes": [dict(r) for r in node_recs],
        "edges": [dict(r) for r in edge_recs],
    }


@app.get("/api/evidence")
def evidence():
    with get_driver() as driver:
        return run_query(driver, "evidence")


@app.get("/api/insights")
def insights():
    from app.insights import workspace_insights
    try:
        return workspace_insights()
    except Exception as exc:
        raise HTTPException(503, "insights unavailable") from exc


@app.get("/api/conflicts")
def conflicts():
    from app.insights import list_conflicts
    try:
        return list_conflicts()
    except Exception as exc:
        raise HTTPException(503, "conflicts unavailable") from exc


@app.get("/api/runs")
def runs(limit: int = 50):
    from app.insights import list_runs
    try:
        return list_runs(limit=min(max(limit, 1), 200))
    except Exception as exc:
        raise HTTPException(503, "runs unavailable") from exc


@app.get("/api/export")
def export_workspace():
    from app.insights import export_workspace as build_export
    try:
        payload = build_export()
    except Exception as exc:
        raise HTTPException(503, "export unavailable") from exc
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return JSONResponse(payload, headers={"Content-Disposition": f'attachment; filename="verigraph-export-{stamp}.json"'})


class CompareBody(BaseModel):
    run_a: str
    run_b: str


@app.post("/api/compare")
def compare_endpoint(body: CompareBody, request: Request):
    authorize_expensive_request(request, bucket="analysis", default_limit=30)
    from app.research_tools import compare_runs
    try:
        return compare_runs(body.run_a, body.run_b)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/timeline")
def timeline_endpoint():
    from app.research_tools import claim_timeline
    try:
        return claim_timeline()
    except Exception as exc:
        raise HTTPException(503, "timeline unavailable") from exc


@app.get("/api/evidence-brief")
def evidence_brief_endpoint():
    from app.research_tools import evidence_brief_markdown
    try:
        markdown = evidence_brief_markdown()
    except Exception as exc:
        raise HTTPException(503, "evidence brief unavailable") from exc
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return PlainTextResponse(markdown, media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="verigraph-brief-{stamp}.md"'})


@app.get("/api/batch-plan")
def batch_plan():
    from app.research_tools import methods_never_run
    pending = methods_never_run()
    return {"pending": pending, "count": len(pending)}


class BatchRunBody(BaseModel):
    method_ids: list[str] = Field(default_factory=list)
    backend: str = "auto"
    params: dict = Field(default_factory=dict)


@app.post("/api/batch-run")
def batch_run(body: BatchRunBody | None, request: Request):
    authorize_expensive_request(request, bucket="run", default_limit=5)
    from app.research_tools import methods_never_run
    body = body or BatchRunBody()
    backend = body.backend if body.backend in {"auto", "local", "daytona"} else "auto"
    targets = (body.method_ids or methods_never_run())[:32]
    results = []
    for method_id in targets:
        try:
            record = _execute_and_curate(method_id, backend, body.params or {})
            results.append({"method_id": method_id, "run_id": record.get("run_id"), "error": record.get("error"), "metrics": (record.get("result") or {}).get("metrics", {})})
        except Exception as exc:
            results.append({"method_id": method_id, "error": str(exc)})
    return {"ok": True, "ran": results, "count": len(results)}


class WorkspaceSaveBody(BaseModel):
    name: str
    snapshot: dict = Field(default_factory=dict)
    visitor_id: str = ""
    email: str = ""
    display_name: str = ""


@app.get("/api/saved-workspaces")
def list_saved_workspaces(visitor: str = "", email: str = ""):
    try:
        from app.butterbase import list_workspaces
        return {"workspaces": list_workspaces(visitor_id=visitor, email=email)}
    except Exception as exc:
        raise HTTPException(503, "saved workspaces unavailable") from exc


@app.post("/api/saved-workspaces")
def save_workspace(body: WorkspaceSaveBody, request: Request):
    authorize_expensive_request(request, bucket="workspace", default_limit=10)
    name = body.name.strip()
    if not name or len(name) > 80:
        raise HTTPException(400, "workspace name required (max 80 chars)")
    try:
        from app.butterbase import save_workspace as cloud_save
        return {"ok": True, "workspace": cloud_save(name=name, snapshot=body.snapshot, visitor_id=body.visitor_id or None, email=body.email or None)}
    except Exception as exc:
        raise HTTPException(503, "save workspace unavailable") from exc


@app.delete("/api/saved-workspaces/{workspace_id}")
def delete_saved_workspace(workspace_id: str, request: Request, visitor: str = ""):
    authorize_expensive_request(request, bucket="workspace", default_limit=10)
    try:
        from app.butterbase import delete_workspace
        delete_workspace(workspace_id, visitor_id=visitor or None)
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(503, "delete workspace unavailable") from exc


class RunBody(BaseModel):
    params: dict = Field(default_factory=dict)


def _execute_and_curate(method_id: str, backend: str, params: dict) -> dict:
    from app.workspace import active_method_guard

    with active_method_guard(method_id):
        record = execute(method_id, backend, params=params)
        curate(record)
        try:  # best-effort mirror of both the run and authoritative workspace state
            from app.butterbase import sync_workspace

            sync_workspace()
        except Exception as exc:
            record["butterbase_sync_error"] = type(exc).__name__
        return record


@app.post("/api/run/{method_id}")
async def run_method(
    method_id: str,
    request: Request,
    backend: str = "auto",
    body: RunBody | None = None,
):
    authorize_expensive_request(request, bucket="execution", default_limit=5)
    if backend not in ("auto", "local", "daytona"):
        raise HTTPException(400, "backend must be auto|local|daytona")
    from app.validation import normalize_parameter_overrides, require_method_id

    try:
        method_id = require_method_id(method_id)
        params = normalize_parameter_overrides(body.params if body else {})
        async with _execution_slots:
            return await asyncio.to_thread(_execute_and_curate, method_id, backend, params)
    except NotImplementedError as e:
        raise HTTPException(404, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


def _ingest_paper_text(text: str) -> dict:
    """Paper text -> validated extraction -> atomic workspace + graph update."""
    from app.extract import extract_live_text
    from app.workspace import persist_paper_and_activate

    if len(text.strip()) < 200:
        raise HTTPException(400, "paper text too short — need at least the abstract and method section")
    data = extract_live_text(text)
    result = persist_paper_and_activate(data, text)
    workspace = result["workspace"]
    if workspace.get("partial") or not workspace.get("ok"):
        detail = workspace.get("message") or workspace.get("sync", {}).get(
            "error", "workspace graph synchronization is pending recovery"
        )
        raise HTTPException(503, f"paper persisted but activation needs recovery: {detail}")

    pid = result["paper_id"]
    mirrors: dict[str, str] = {}

    try:  # best-effort authoritative workspace snapshot
        from app.butterbase import sync_workspace

        sync_workspace()
        mirrors["butterbase"] = "synchronized"
    except Exception as exc:
        mirrors["butterbase"] = f"deferred: {type(exc).__name__}"

    try:
        from app.cognee_memory import remember_paper_sync

        remember_paper_sync(pid, data["paper"]["title"], text, data)
        mirrors["cognee"] = "synchronized"
    except Exception as exc:
        mirrors["cognee"] = f"deferred: {type(exc).__name__}"

    result["mirrors"] = mirrors
    return result


def _pdf_to_text(blob: bytes) -> str:
    """Parse an untrusted PDF outside the API process with hard resource caps."""
    import signal
    import subprocess
    import tempfile

    worker = os.path.join(ROOT, "app", "pdf_extract.py")
    env = {
        name: os.environ[name]
        for name in ("LANG", "LC_ALL", "PATH", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR")
        if name in os.environ
    }
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})
    with tempfile.TemporaryDirectory(prefix="verigraph-pdf-") as directory:
        stdout_path = os.path.join(directory, "stdout")
        stderr_path = os.path.join(directory, "stderr")
        with open(stdout_path, "w+b") as stdout_file, open(stderr_path, "w+b") as stderr_file:
            proc = subprocess.Popen(
                [sys.executable, "-I", worker],
                stdin=subprocess.PIPE,
                stdout=stdout_file,
                stderr=stderr_file,
                env=env,
                start_new_session=True,
            )
            try:
                proc.communicate(input=blob, timeout=45)
            except subprocess.TimeoutExpired as exc:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (AttributeError, OSError):
                    proc.kill()
                proc.wait()
                raise ValueError("PDF parsing exceeded 45 seconds") from exc
            finally:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (AttributeError, ProcessLookupError, OSError):
                    pass
            stdout_file.seek(0)
            text = stdout_file.read(2_000_001).decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise ValueError("PDF parser rejected the document")
    if len(text) > 2_000_000:
        raise ValueError("extracted PDF text exceeds the 2 MB limit")
    return text


class Upload(BaseModel):
    text: str = Field(min_length=200, max_length=2_000_000)


@app.post("/api/upload")
async def upload(body: Upload, request: Request):
    authorize_expensive_request(request, bucket="ingestion", default_limit=5)
    async with _ingestion_slots:
        return await asyncio.to_thread(_ingest_paper_text, body.text)


@app.post("/api/upload-file")
async def upload_file(request: Request, file: UploadFile):
    authorize_expensive_request(request, bucket="ingestion", default_limit=5)
    async with _ingestion_slots:
        blob = await file.read(25_000_001)
        if len(blob) > 25_000_000:
            raise HTTPException(413, "file too large (25 MB max)")
        if (file.filename or "").lower().endswith(".pdf") or blob[:4] == b"%PDF":
            try:
                text = await asyncio.to_thread(_pdf_to_text, blob)
            except Exception as e:
                raise HTTPException(400, f"could not parse PDF: {e}")
        else:
            text = blob.decode(errors="replace")
        if len(text) > 2_000_000:
            raise HTTPException(413, "extracted paper text exceeds the 2 MB limit")
        return await asyncio.to_thread(_ingest_paper_text, text)


class ArxivUpload(BaseModel):
    url: str = Field(min_length=1, max_length=500)


@app.post("/api/upload-arxiv")
async def upload_arxiv(body: ArxivUpload, request: Request):
    authorize_expensive_request(request, bucket="ingestion", default_limit=5)
    from app.arxiv import fetch_arxiv_text, parse_arxiv_id

    try:
        parse_arxiv_id(body.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    async with _ingestion_slots:
        try:
            text = await asyncio.to_thread(fetch_arxiv_text, body.url)
        except Exception as e:
            raise HTTPException(502, f"arXiv fetch failed: {e}")
        return await asyncio.to_thread(_ingest_paper_text, text)


CONDUCT_PIPE = os.path.join(ROOT, "pipelines", "paper-orchestrator.pipe")


class Conduct(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)


def _method_exists(method_id: str) -> bool:
    with get_driver() as driver:
        recs, _, _ = driver.execute_query(
            "MATCH (m:Method:Verigraph {id: $id, verigraph_namespace: $graph_namespace}) "
            "RETURN count(m) AS c",
            id=method_id, graph_namespace=GRAPH_NAMESPACE, database_=DATABASE)
        return recs[0]["c"] == 1


@app.post("/api/conduct")
async def conduct(body: Conduct, request: Request):
    """Conductor workflow: investigator → execute → reporter.

    Uses RocketRide specialist pipes when the engine is up; falls back to
    graph-grounded Python audit + canonical execute spine otherwise."""
    from app.grounded_qa import agent_unavailable, answer_brief, answer_conduct

    authorize_expensive_request(request, bucket="agent", default_limit=20)
    if os.environ.get("GROUNDED_AGENTS", "").lower() in ("1", "true", "yes"):
        async with _execution_slots:
            return await asyncio.to_thread(answer_conduct)

    steps = []
    investigation_prompt = f"{INVESTIGATE_PROMPT}\n\nUser objective: {body.message.strip()}"

    # STEP 1 — Investigator (checks V1/V3/V4, retry once)
    parsed = _parse_p2r_block(await _agent_chat(INVESTIGATE_PIPE, investigation_prompt))
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
                MATCH (m:Method:Verigraph {verigraph_namespace: $graph_namespace})
                WHERE NOT EXISTS {
                  MATCH (:Run:Verigraph {verigraph_namespace: $graph_namespace})
                        -[:IMPLEMENTS]->(m)
                }
                RETURN m.id AS id LIMIT 1
                """, graph_namespace=GRAPH_NAMESPACE, database_=DATABASE)
            method_id = recs[0]["id"] if recs else "wilson2017-m1"
        steps.append(f"[investigator] ✗ id check failed twice — fell back to "
                     f"deterministic pick: {method_id}")
    else:
        steps.append(f"[investigator] ✓ {len(payload.get('conflicts') or [])} conflicts, "
                     f"{len(payload.get('untested_claims') or [])} untested — "
                     f"recommended {method_id}")

    # STEP 2 — Executor: canonical spine (codegen -> sandbox -> curate)
    async with _execution_slots:
        record = await asyncio.to_thread(_execute_and_curate, method_id, "auto", {})
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
    question: str = Field(min_length=1, max_length=4_000)


# One shared RocketRide connection; one cached task token per .pipe file.
# The engine refuses a second `use` of a running pipeline ("Pipeline is
# already running"), so we start each once and attach on later requests.
_rr: dict = {"client": None, "tokens": {}, "chat_locks": {}}
_rr_guard = asyncio.Lock()


async def _pipeline_token(pipe_path: str):
    import json as _json

    from rocketride import RocketRideClient

    async with _rr_guard:
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
                if _pipeline_uses_graph(pipe_path):
                    raise RuntimeError(
                        "A pre-existing graph pipeline cannot be trusted after credential changes; "
                        "restart RocketRide before enabling it."
                    ) from e
                with open(pipe_path) as f:
                    project_id = _json.load(f)["project_id"]
                token = await client.get_task_token(project_id, "chat_1")
                if not token:
                    raise
                _rr["tokens"][pipe_path] = token
        _rr["chat_locks"].setdefault(pipe_path, asyncio.Lock())
        return client, _rr["tokens"][pipe_path]


def _pipeline_uses_graph(pipe_path: str) -> bool:
    """Fail closed when a pipeline is malformed or its providers are unknown."""
    import json as _json

    try:
        with open(pipe_path, encoding="utf-8") as handle:
            pipeline = _json.load(handle)
    except (OSError, ValueError):
        return True
    nodes = (
        pipeline.get("components")
        or pipeline.get("pipeline")
        or pipeline.get("nodes")
        or []
    )
    if not isinstance(nodes, list):
        return True
    return any(
        isinstance(node, dict) and node.get("provider") == "db_neo4j"
        for node in nodes
    )


def _assert_rocketride_readonly_credentials() -> None:
    """Prove the RocketRide Neo4j identity cannot execute mutation clauses."""
    from neo4j import GraphDatabase
    from neo4j.exceptions import ClientError

    enabled = os.environ.get("VERIGRAPH_ENABLE_ROCKETRIDE_DB", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        raise RuntimeError("RocketRide graph access is disabled")

    uri = os.environ.get("ROCKETRIDE_NEO4J_URI", "").strip()
    username = os.environ.get("ROCKETRIDE_NEO4J_READONLY_USER", "").strip()
    password = os.environ.get("ROCKETRIDE_NEO4J_READONLY_PASSWORD", "")
    database = os.environ.get("ROCKETRIDE_NEO4J_DATABASE", "").strip()
    graph_namespace = os.environ.get("ROCKETRIDE_GRAPH_NAMESPACE", "").strip()
    if not uri or not username or not password or not database or not graph_namespace:
        raise RuntimeError("Dedicated RocketRide read-only credentials are not configured")
    if username == os.environ.get("NEO4J_USERNAME", "").strip():
        raise RuntimeError("RocketRide must not reuse the Verigraph write identity")
    if graph_namespace != GRAPH_NAMESPACE:
        raise RuntimeError(
            "ROCKETRIDE_GRAPH_NAMESPACE must match VERIGRAPH_GRAPH_NAMESPACE"
        )

    mutation_probes = (
        "CREATE (n:VerigraphReadonlyProbe) RETURN count(n) AS c",
        "MATCH (n:Verigraph) WITH n LIMIT 1 SET n.__readonly_probe = true RETURN count(n) AS c",
        "MATCH (n:Verigraph) WITH n LIMIT 1 DETACH DELETE n RETURN count(*) AS c",
        "MATCH (a:Verigraph), (b:Verigraph) WITH a, b LIMIT 1 "
        "CREATE (a)-[:VERIGRAPH_READONLY_PROBE]->(b) RETURN count(*) AS c",
    )
    driver = GraphDatabase.driver(uri, auth=(username, password), connection_timeout=10)
    try:
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            foreign = session.run(
                """
                MATCH (n:Verigraph)
                WHERE n.verigraph_namespace IS NULL
                   OR n.verigraph_namespace <> $graph_namespace
                RETURN count(n) AS count
                """,
                graph_namespace=GRAPH_NAMESPACE,
            ).single()
        if foreign is None or int(foreign["count"]) != 0:
            raise RuntimeError(
                "RocketRide database contains Verigraph nodes outside the configured namespace"
            )
        for cypher in mutation_probes:
            with driver.session(database=database) as session:
                tx = session.begin_transaction()
                try:
                    tx.run(cypher).consume()
                except ClientError as exc:
                    if exc.code != "Neo.ClientError.Security.Forbidden":
                        raise RuntimeError("RocketRide read-only verification failed") from exc
                else:
                    raise RuntimeError("RocketRide Neo4j identity permits graph mutations")
                finally:
                    try:
                        tx.rollback()
                    except Exception:
                        pass
    finally:
        driver.close()


async def _agent_chat(pipe_path: str, question_text: str) -> str:
    from rocketride.schema import Question

    try:
        if _pipeline_uses_graph(pipe_path):
            await asyncio.to_thread(_assert_rocketride_readonly_credentials)
        client, token = await _pipeline_token(pipe_path)
        q = Question()
        q.addQuestion(question_text)
        async with _rr["chat_locks"][pipe_path]:
            response = await client.chat(token=token, question=q)
    except Exception as e:
        # drop the cached session so the next request reconnects fresh
        stale = _rr.pop("client", None)
        _rr.update({"client": None, "tokens": {}, "chat_locks": {}})
        if stale is not None:
            try:
                await stale.disconnect()
            except Exception:
                pass
        return f"(agent unavailable: {type(e).__name__})"

    answers = response.get("answers", [])
    if not answers:
        for key, lane in response.get("result_types", {}).items():
            if lane == "answers":
                answers = response.get(key, [])
                break
    answer = answers[0] if answers else "(no answer from agent)"

    return answer


async def close_rocketride_client():
    stale = _rr.get("client")
    _rr.update({"client": None, "tokens": {}, "chat_locks": {}})
    if stale is not None:
        try:
            await stale.disconnect()
        except Exception:
            pass


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
async def ask(body: Ask, request: Request):
    from app.cognee_memory import is_enabled, recall_context
    from app.grounded_qa import agent_unavailable, answer_ask

    authorize_expensive_request(request, bucket="agent", default_limit=20)
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "question is required")
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
        answer = await asyncio.to_thread(answer_ask, question)
    if memory and not answer.startswith("[semantic") and not answer.startswith("**Cognee"):
        answer = f"[semantic memory ✓]\n\n{answer}"
    try:
        from app.cognee_memory import log_session_qa_sync

        visitor_id = request.query_params.get("visitor", "")
        session_id = (
            f"verigraph-{visitor_id}"
            if _VISITOR_ID.fullmatch(visitor_id)
            else None
        )
        await asyncio.to_thread(
            log_session_qa_sync,
            question,
            answer,
            session_id=session_id,
        )
    except Exception:
        pass
    return {"answer": answer, "memory_used": bool(memory), "grounded": grounded}


class MemoryRecall(BaseModel):
    query: str = Field(min_length=1, max_length=4_000)
    paper_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)


@app.post("/api/memory/recall")
async def memory_recall(body: MemoryRecall, request: Request):
    from app.cognee_memory import is_enabled, recall

    authorize_expensive_request(request, bucket="agent", default_limit=20)
    if body.paper_id:
        from app.validation import require_paper_id

        require_paper_id(body.paper_id)
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
    method_id: str = Field(min_length=1, max_length=96)
    params: dict = Field(default_factory=dict)


@app.post("/api/execute")
async def execute_agent(body: ExecuteBody, request: Request):
    """Executor sub-agent path: agent drives POST /api/run via its HTTP tool."""
    authorize_expensive_request(request, bucket="agent", default_limit=20)
    from app.validation import normalize_parameter_overrides, require_method_id

    method_id = require_method_id(body.method_id)
    params = normalize_parameter_overrides(body.params)
    prompt = (f"Run method {method_id} now"
              + (f" with parameter overrides {params}" if params else "")
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
async def investigate(request: Request):
    from app.grounded_qa import agent_unavailable, answer_investigate

    authorize_expensive_request(request, bucket="agent", default_limit=20)
    answer = await _agent_chat(INVESTIGATE_PIPE, INVESTIGATE_PROMPT)
    parsed = _parse_p2r_block(answer)
    if agent_unavailable(parsed.get("prose") or answer):
        return await asyncio.to_thread(answer_investigate)
    return {"answer": parsed["prose"] or answer, **{k: parsed[k] for k in
            ("agent", "status", "payload", "header_present")}}


@app.post("/api/brief")
async def brief(request: Request):
    from app.grounded_qa import agent_unavailable, answer_brief

    authorize_expensive_request(request, bucket="agent", default_limit=20)
    def _run_count() -> int:
        with get_driver() as driver:
            recs, _, _ = driver.execute_query(
                "MATCH (r:Run:Verigraph {verigraph_namespace: $graph_namespace}) "
                "RETURN count(r) AS c",
                graph_namespace=GRAPH_NAMESPACE,
                database_=DATABASE)
            return recs[0]["c"]

    if await asyncio.to_thread(_run_count) == 0:
        raise HTTPException(409, "no runs in the graph yet — run a method first")
    answer = await _agent_chat(BRIEF_PIPE, BRIEF_PROMPT)
    parsed = _parse_p2r_block(answer)
    if agent_unavailable(parsed.get("prose") or answer):
        return await asyncio.to_thread(answer_brief)
    return {"answer": parsed["prose"] or answer, **{k: parsed[k] for k in
            ("agent", "status", "payload", "header_present")}}
