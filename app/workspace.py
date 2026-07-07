"""Workspace management: blank slate or bundled demo papers."""

import json
import os
import shutil

from app.db import DATABASE, get_driver
from app.extract import EXTRACTED_DIR, PAPERS_DIR
from app.graph import load_claim_relations, load_paper, reset_our_graph
from app.queries import run_query

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMPL_DIR = os.path.join(ROOT, "papers", "impl")
BUNDLED_DIR = os.path.join(ROOT, "papers", "bundled")
BUNDLED_EXTRACTED = os.path.join(BUNDLED_DIR, "extracted")
BUNDLED_TEXTS = os.path.join(BUNDLED_DIR, "texts")
BUNDLED_IMPL = os.path.join(BUNDLED_DIR, "impl")

# Core hackathon demo: three papers that disagree about Adam
DEMO_PAPER_IDS = ("adam2014", "wilson2017", "adamw2017")


def _clear_dir(path: str) -> None:
    if not os.path.isdir(path):
        return
    for name in os.listdir(path):
        fp = os.path.join(path, name)
        if os.path.isfile(fp):
            os.remove(fp)


def clear_workspace_files() -> None:
    """Remove active workspace papers (extracted JSON, source texts, impl)."""
    _clear_dir(EXTRACTED_DIR)
    _clear_dir(IMPL_DIR)
    for name in os.listdir(PAPERS_DIR):
        if name.endswith(".txt") and os.path.isfile(os.path.join(PAPERS_DIR, name)):
            os.remove(os.path.join(PAPERS_DIR, name))


def clear_run_dirs() -> None:
    for sub in ("runs", "generated"):
        path = os.path.join(ROOT, sub)
        if os.path.isdir(path):
            shutil.rmtree(path)


def copy_bundled_to_workspace(paper_ids: tuple[str, ...] | None = DEMO_PAPER_IDS) -> int:
    """Restore bundled papers into the active workspace. Returns paper count."""
    if not os.path.isdir(BUNDLED_EXTRACTED):
        raise FileNotFoundError(f"bundled papers missing: {BUNDLED_EXTRACTED}")
    allowed = set(paper_ids) if paper_ids is not None else None
    os.makedirs(EXTRACTED_DIR, exist_ok=True)
    os.makedirs(IMPL_DIR, exist_ok=True)
    count = 0
    for name in sorted(os.listdir(BUNDLED_EXTRACTED)):
        if not name.endswith(".json"):
            continue
        pid = name[:-5]
        if allowed is not None and pid not in allowed:
            continue
        shutil.copy2(os.path.join(BUNDLED_EXTRACTED, name), os.path.join(EXTRACTED_DIR, name))
        count += 1
    if os.path.isdir(BUNDLED_TEXTS):
        for name in os.listdir(BUNDLED_TEXTS):
            if not name.endswith(".txt"):
                continue
            pid = name[:-4]
            if allowed is not None and pid not in allowed:
                continue
            shutil.copy2(os.path.join(BUNDLED_TEXTS, name), os.path.join(PAPERS_DIR, name))
    if os.path.isdir(BUNDLED_IMPL):
        for name in os.listdir(BUNDLED_IMPL):
            if not name.endswith(".py"):
                continue
            mid = name[:-3]
            paper_prefix = mid.rsplit("-", 1)[0] if "-m" in name else mid
            if allowed is not None and paper_prefix not in allowed:
                continue
            shutil.copy2(os.path.join(BUNDLED_IMPL, name), os.path.join(IMPL_DIR, name))
    return count


def load_extracted_to_graph() -> dict:
    """Load every JSON in papers/extracted into Neo4j."""
    files = sorted(f for f in os.listdir(EXTRACTED_DIR) if f.endswith(".json"))
    datas = []
    with get_driver() as driver, driver.session(database=DATABASE) as session:
        for fname in files:
            with open(os.path.join(EXTRACTED_DIR, fname)) as f:
                data = json.load(f)
            session.execute_write(load_paper, data)
            datas.append(data)
        for data in datas:
            session.execute_write(load_claim_relations, data)
        rows = run_query(driver, "evidence")
    stats = {
        "papers": len(files),
        "claims": len(rows),
        "methods": sum(len(d.get("methods", [])) for d in datas),
    }
    return stats


def _paper_manifest() -> list[dict]:
    if not os.path.isdir(EXTRACTED_DIR):
        return []
    out = []
    for fname in sorted(f for f in os.listdir(EXTRACTED_DIR) if f.endswith(".json")):
        with open(os.path.join(EXTRACTED_DIR, fname)) as f:
            data = json.load(f)
        p = data["paper"]
        out.append({
            "id": p["id"],
            "title": p["title"],
            "year": p.get("year"),
            "arxiv": p.get("arxiv"),
            "claims": len(data.get("claims", [])),
            "methods": len(data.get("methods", [])),
        })
    return out


def _clear_runs_for_methods(method_ids: list[str]) -> int:
    runs_dir = os.path.join(ROOT, "runs")
    if not method_ids or not os.path.isdir(runs_dir):
        return 0
    allowed = set(method_ids)
    removed = 0
    for fname in os.listdir(runs_dir):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(runs_dir, fname)
        try:
            with open(path) as f:
                rec = json.load(f)
            if rec.get("method_id") in allowed:
                os.remove(path)
                removed += 1
        except (OSError, json.JSONDecodeError):
            continue
    gen_dir = os.path.join(ROOT, "generated")
    if os.path.isdir(gen_dir):
        for name in os.listdir(gen_dir):
            fp = os.path.join(gen_dir, name)
            if not os.path.isfile(fp):
                continue
            if any(name.startswith(f"{mid}-") or name.startswith(mid) for mid in allowed):
                os.remove(fp)
    return removed


def _delete_paper_files(paper_id: str, method_ids: list[str]) -> None:
    for path in (
        os.path.join(EXTRACTED_DIR, f"{paper_id}.json"),
        os.path.join(PAPERS_DIR, f"{paper_id}.txt"),
    ):
        if os.path.isfile(path):
            os.remove(path)
    if os.path.isdir(IMPL_DIR):
        for name in os.listdir(IMPL_DIR):
            if not name.endswith(".py"):
                continue
            if name.startswith(f"{paper_id}-") or name == f"{paper_id}.py":
                os.remove(os.path.join(IMPL_DIR, name))


def _delete_paper_from_graph(driver, paper_id: str, method_ids: list[str], claim_ids: list[str]) -> None:
    if method_ids:
        driver.execute_query(
            """
            MATCH (m:Method) WHERE m.id IN $mids
            OPTIONAL MATCH (r:Run)-[:IMPLEMENTS]->(m)
            OPTIONAL MATCH (r)-[:PRODUCED]->(a:Artifact)
            DETACH DELETE r, a
            """,
            mids=method_ids, database_=DATABASE,
        )
    if claim_ids:
        driver.execute_query(
            "MATCH (c:Claim) WHERE c.id IN $cids DETACH DELETE c",
            cids=claim_ids, database_=DATABASE,
        )
    if method_ids:
        driver.execute_query(
            "MATCH (m:Method) WHERE m.id IN $mids DETACH DELETE m",
            mids=method_ids, database_=DATABASE,
        )
    driver.execute_query(
        """
        MATCH (p:Paper {id: $pid})-[:EVALUATED_ON]->(d:Dataset)
        WHERE NOT EXISTS {
            MATCH (other:Paper)-[:EVALUATED_ON]->(d) WHERE other.id <> $pid
        }
        DETACH DELETE d
        """,
        pid=paper_id, database_=DATABASE,
    )
    driver.execute_query(
        "MATCH (p:Paper {id: $pid}) DETACH DELETE p",
        pid=paper_id, database_=DATABASE,
    )
    driver.execute_query(
        "MATCH (a:Author) WHERE NOT (a)-[:WROTE]->() DELETE a",
        database_=DATABASE,
    )


def remove_paper(paper_id: str) -> dict:
    """Remove one paper from the workspace (files, runs, graph nodes)."""
    path = os.path.join(EXTRACTED_DIR, f"{paper_id}.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"paper not in workspace: {paper_id}")

    with open(path) as f:
        data = json.load(f)
    method_ids = [m["id"] for m in data.get("methods", [])]
    claim_ids = [c["id"] for c in data.get("claims", [])]
    title = data["paper"]["title"]

    runs_removed = _clear_runs_for_methods(method_ids)
    _delete_paper_files(paper_id, method_ids)

    with get_driver() as driver:
        _delete_paper_from_graph(driver, paper_id, method_ids, claim_ids)
        rows = run_query(driver, "evidence")
        remaining = _paper_manifest()

    return {
        "removed": paper_id,
        "title": title,
        "runs_removed": runs_removed,
        "empty": len(remaining) == 0,
        "papers": len(remaining),
        "claims": len(rows),
        "paper_ids": [p["id"] for p in remaining],
        "papers_detail": remaining,
        "message": f"Removed {paper_id} from workspace.",
    }


def workspace_status() -> dict:
    manifest = _paper_manifest()
    files = [f for f in os.listdir(EXTRACTED_DIR) if f.endswith(".json")] if os.path.isdir(EXTRACTED_DIR) else []
    empty = len(files) == 0
    with get_driver() as driver:
        rows = run_query(driver, "evidence") if not empty else []
        runs, _, _ = driver.execute_query("MATCH (n:Run) RETURN count(n) AS c", database_=DATABASE)
    return {
        "empty": empty,
        "papers": len(files),
        "claims": len(rows),
        "runs": runs[0]["c"] if runs else 0,
        "paper_ids": [f[:-5] for f in sorted(files)],
        "papers_detail": manifest,
    }


def new_workspace() -> dict:
    """Blank workspace: wipe graph and local papers; user adds their own."""
    with get_driver() as driver:
        reset_our_graph(driver)
    clear_workspace_files()
    clear_run_dirs()
    return {"empty": True, "papers": 0, "claims": 0, "message": "New workspace ready — add your first paper."}


def load_demo_workspace() -> dict:
    """Load bundled demo papers into a fresh graph (no runs)."""
    with get_driver() as driver:
        reset_our_graph(driver)
    clear_workspace_files()
    clear_run_dirs()
    bundled = copy_bundled_to_workspace()
    stats = load_extracted_to_graph()
    stats["empty"] = False
    stats["bundled"] = bundled
    stats["message"] = f"Demo loaded — {stats['papers']} papers (Adam · Wilson · AdamW), no runs yet."
    return stats
