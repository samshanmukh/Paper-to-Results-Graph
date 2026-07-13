"""Reset runtime evidence while preserving every checked-in paper asset."""

from app.workspace import reset_workspace_runs


def reset_demo_state() -> dict:
    """Archive runs and rebuild the graph from the active workspace manifest."""
    return reset_workspace_runs()
