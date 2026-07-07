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


def copy_bundled_to_workspace() -> int:
    """Restore bundled demo papers into the active workspace. Returns paper count."""
    if not os.path.isdir(BUNDLED_EXTRACTED):
        raise FileNotFoundError(f"bundled papers missing: {BUNDLED_EXTRACTED}")
    os.makedirs(EXTRACTED_DIR, exist_ok=True)
    os.makedirs(IMPL_DIR, exist_ok=True)
    count = 0
    for name in sorted(os.listdir(BUNDLED_EXTRACTED)):
        if not name.endswith(".json"):
            continue
        shutil.copy2(os.path.join(BUNDLED_EXTRACTED, name), os.path.join(EXTRACTED_DIR, name))
        count += 1
    if os.path.isdir(BUNDLED_TEXTS):
        for name in os.listdir(BUNDLED_TEXTS):
            if name.endswith(".txt"):
                shutil.copy2(os.path.join(BUNDLED_TEXTS, name), os.path.join(PAPERS_DIR, name))
    if os.path.isdir(BUNDLED_IMPL):
        for name in os.listdir(BUNDLED_IMPL):
            if name.endswith(".py"):
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


def workspace_status() -> dict:
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
    stats["message"] = f"Demo workspace loaded — {stats['papers']} papers, no runs yet."
    return stats
