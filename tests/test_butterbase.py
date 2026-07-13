import threading
from contextlib import contextmanager, nullcontext
from unittest.mock import MagicMock, call, patch

import pytest

from app.butterbase import _paper_row, sync_run, sync_workspace, upsert


FINGERPRINT = "a" * 64
CONTEXT = "b" * 64


def test_sync_run_persists_created_at_provenance_and_parameters():
    record = {
        "run_id": "run-paper-m1-test",
        "method_id": "paper-m1",
        "backend": "daytona",
        "exit_code": 0,
        "duration_s": 1.2,
        "created_at": "2026-01-01T00:00:00+00:00",
        "implementation_source": "llm",
        "implementation_fingerprint": FINGERPRINT,
        "context_digest": CONTEXT,
        "workspace_revision": 7,
        "provisional": True,
        "params": {"steps": 10, "learning_rate": 0.1},
        "parameter_overrides": {"steps": 10},
        "error": None,
        "stdout": "result",
        "result": {
            "params": {"steps": 10, "learning_rate": 0.1},
            "metrics": {"score": 1.0},
            "claim_checks": [
                {"claim_id": "paper-c1", "verdict": "VALIDATES", "detail": "score=1"}
            ],
        },
    }
    with (
        patch("app.curator.validate_run_record", return_value=record),
        patch("app.butterbase.upsert", return_value="inserted") as upsert,
    ):
        assert sync_run(record) == "inserted"

    table, row = upsert.call_args.args
    assert table == "runs"
    assert row["created_at"] == record["created_at"]
    assert row["implementation_source"] == "llm"
    assert row["implementation_fingerprint"] == FINGERPRINT
    assert row["context_digest"] == CONTEXT
    assert row["workspace_revision"] == 7
    assert row["active"] is True
    assert row["provisional"] is True
    assert row["params"] == {"steps": 10, "learning_rate": 0.1}
    assert row["parameter_overrides"] == {"steps": 10}


def test_sync_run_rejects_non_object_parameter_payloads():
    record = {
        "run_id": "run-paper-m1-test",
        "method_id": "paper-m1",
        "backend": "daytona",
        "exit_code": 1,
        "created_at": "2026-01-01T00:00:00+00:00",
        "params": [],
        "error": "failed",
        "result": None,
    }
    with patch("app.butterbase.upsert") as write:
        with pytest.raises(
            ValueError, match="params and parameter_overrides must be objects"
        ):
            sync_run(record)
    write.assert_not_called()


def test_upsert_patches_existing_text_primary_key_instead_of_skipping():
    row = {"id": "paper-1", "title": "revised", "active": True}
    with patch(
        "app.butterbase._req",
        side_effect=[[{"id": "paper-1", "title": "old", "active": True}], {}],
    ) as request:
        assert upsert("papers", row) == "updated"
    assert request.call_args_list == [
        call("GET", "/papers?id=eq.paper-1&limit=1"),
        call("PATCH", "/papers/paper-1", {"title": "revised", "active": True}),
    ]


def test_same_id_paper_revision_changes_content_digest():
    entry = {"id": "paper-1", "source": {"kind": "runtime", "object": "c" * 64}}
    first = {
        "paper": {"id": "paper-1", "title": "First", "year": 2026},
        "claims": [],
        "methods": [],
    }
    second = {**first, "paper": {**first["paper"], "title": "Second"}}
    first_row = _paper_row(first, entry, 1)
    second_row = _paper_row(second, entry, 2)
    assert first_row["id"] == second_row["id"]
    assert first_row["content_digest"] != second_row["content_digest"]
    assert second_row["workspace_revision"] == 2


def test_workspace_sync_publishes_active_paper_revisions_and_run_ids_last():
    paper = {
        "paper": {"id": "paper-1", "title": "Paper", "year": 2026},
        "claims": [],
        "methods": [],
    }
    run = {"run_id": "run-1", "method_id": "paper-1-m1"}
    snapshot = {
        "manifest": {
            "version": 1,
            "revision": 9,
            "active_papers": [{"id": "paper-1", "source": {"kind": "tracked"}}],
        },
        "entries": [{"id": "paper-1", "source": {"kind": "tracked"}}],
        "papers": [paper],
        "runs": [run],
        "identity": "d" * 64,
    }
    store = MagicMock()
    store.lock.return_value = nullcontext()
    with (
        patch(
            "app.butterbase._workspace_snapshot_unlocked", return_value=snapshot
        ) as take_snapshot,
        patch("app.butterbase._sync_paper_rows", return_value=1) as sync_papers,
        patch("app.butterbase.sync_run", return_value="updated") as sync_one_run,
        patch(
            "app.butterbase._req",
            return_value=[{"id": "run-before-reset", "active": True}],
        ),
        patch("app.butterbase.upsert", return_value="updated") as write_state,
        patch("app.workspace.get_workspace_store", return_value=store),
    ):
        result = sync_workspace()

    take_snapshot.assert_called_once_with(store)
    sync_papers.assert_called_once_with(snapshot)
    sync_one_run.assert_called_once_with(run)
    write_state.assert_any_call("runs", {"id": "run-before-reset", "active": False})
    table, state = write_state.call_args.args
    assert table == "workspace_state"
    assert state["revision"] == 9
    assert state["active_papers"]["items"][0]["id"] == "paper-1"
    assert state["active_run_ids"] == {"items": ["run-1"]}
    assert result == {"papers": 1, "runs": 1, "revision": 9, "state": "updated"}


def test_overlapping_workspace_syncs_serialize_reconciliation_and_state_publication():
    class SerialStore:
        def __init__(self):
            self.mutex = threading.Lock()
            self.attempt_guard = threading.Lock()
            self.attempts = 0
            self.second_attempted = threading.Event()
            self.owner = None

        @contextmanager
        def lock(self):
            with self.attempt_guard:
                self.attempts += 1
                if self.attempts == 2:
                    self.second_attempted.set()
            with self.mutex:
                self.owner = threading.get_ident()
                try:
                    yield
                finally:
                    self.owner = None

    def snapshot(revision):
        return {
            "manifest": {"version": 1, "revision": revision, "active_papers": []},
            "entries": [],
            "papers": [],
            "runs": [],
            "identity": str(revision) * 64,
        }

    store = SerialStore()
    snapshots = iter((snapshot(9), snapshot(10)))
    first_reconciling = threading.Event()
    allow_first_to_finish = threading.Event()
    second_snapshot_started = threading.Event()
    snapshot_count = 0
    snapshot_count_lock = threading.Lock()
    published_revisions = []
    results = []
    errors = []

    def take_snapshot(actual_store):
        nonlocal snapshot_count
        assert actual_store is store
        assert store.owner == threading.get_ident()
        with snapshot_count_lock:
            snapshot_count += 1
            call_number = snapshot_count
        if call_number == 2:
            second_snapshot_started.set()
        return next(snapshots)

    def reconcile(snapshot_value):
        assert store.owner == threading.get_ident()
        if snapshot_value["manifest"]["revision"] == 9:
            first_reconciling.set()
            assert allow_first_to_finish.wait(2)
        return 0

    def write_row(table, row):
        assert store.owner == threading.get_ident()
        if table == "workspace_state":
            published_revisions.append(row["revision"])
        return "updated"

    def invoke_sync():
        try:
            results.append(sync_workspace())
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    with (
        patch("app.workspace.get_workspace_store", return_value=store),
        patch("app.butterbase._workspace_snapshot_unlocked", side_effect=take_snapshot),
        patch("app.butterbase._sync_paper_rows", side_effect=reconcile),
        patch("app.butterbase._all_rows", return_value=[]),
        patch("app.butterbase.upsert", side_effect=write_row),
    ):
        first = threading.Thread(target=invoke_sync)
        second = threading.Thread(target=invoke_sync)
        first.start()
        assert first_reconciling.wait(2)
        second.start()
        assert store.second_attempted.wait(2)
        assert not second_snapshot_started.is_set()
        allow_first_to_finish.set()
        first.join(2)
        second.join(2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert published_revisions == [9, 10]
    assert sorted(result["revision"] for result in results) == [9, 10]


def test_legacy_unknown_run_is_provisional_in_cloud_row():
    record = {
        "run_id": "run-paper-m1-20260101T000000Z",
        "method_id": "paper-m1",
        "backend": "local",
        "exit_code": 1,
        "duration_s": 0.1,
        "created_at": "2026-01-01T00:00:00+00:00",
        "error": "failed",
        "result": None,
    }
    with patch("app.butterbase.upsert", return_value="updated") as write:
        sync_run(record)
    row = write.call_args.args[1]
    assert row["implementation_source"] == "unknown"
    assert row["implementation_fingerprint"] is None
    assert row["provisional"] is True
    assert row["params"] == {}
    assert row["parameter_overrides"] == {}
