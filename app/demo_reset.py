"""Reset demo graph to pristine state (papers loaded, no runs yet)."""

from app.db import DATABASE, get_driver
from app.queries import run_query
from app.workspace import clear_run_dirs, load_extracted_to_graph


def reset_demo_state() -> dict:
    """Delete runs/artifacts, reload current workspace papers, clear local run dirs."""
    with get_driver() as driver:
        for label in ("Run", "Artifact"):
            driver.execute_query(f"MATCH (n:{label}) DETACH DELETE n", database_=DATABASE)
        stats = load_extracted_to_graph()
        rows = run_query(driver, "evidence")
        pristine = all(r["evidence"] == "no runs yet" for r in rows)

    clear_run_dirs()
    stats["pristine"] = pristine
    stats["empty"] = stats["papers"] == 0
    return stats
