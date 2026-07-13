import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import graph, workspace
from app.db import GRAPH_NAMESPACE, GRAPH_OWNER_LABEL
from app.workspace_storage import WorkspaceStorageError, WorkspaceStore


def paper(paper_id: str, method_id: str | None = None) -> dict:
    methods = []
    if method_id:
        methods.append(
            {
                "id": method_id,
                "name": method_id,
                "description": "test method",
                "runnable_hint": "test",
                "params": [],
            }
        )
    return {
        "paper": {
            "id": paper_id,
            "title": f"Paper {paper_id}",
            "year": 2026,
            "arxiv": None,
            "topic": "tests",
            "authors": [],
        },
        "claims": [],
        "methods": methods,
        "datasets": [],
        "cites": [],
        "claim_relations": [],
    }


class WorkspaceFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.extracted = self.root / "papers" / "extracted"
        self.bundled = self.root / "papers" / "bundled" / "extracted"
        self.extracted.mkdir(parents=True)
        self.bundled.mkdir(parents=True)
        (self.root / "runs").mkdir()
        (self.root / "generated").mkdir()
        self.write_paper(self.extracted, paper("alpha", "alpha-m1"))
        self.write_paper(self.extracted, paper("beta", "beta-m1"))
        self.write_paper(self.bundled, paper("alpha", "alpha-m1"))
        self.store = WorkspaceStore(root=self.root)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def write_paper(directory: Path, data: dict) -> Path:
        path = directory / f"{data['paper']['id']}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path


class StorageTests(WorkspaceFixture):
    def test_workspace_lock_is_reentrant_across_store_instances(self):
        same_workspace = WorkspaceStore(root=self.root)
        with self.store.lock():
            with same_workspace.lock():
                self.assertEqual(same_workspace.manifest()["revision"], 0)

    def test_manifest_defaults_to_tracked_assets_without_modifying_them(self):
        before = {path: path.read_bytes() for path in self.extracted.glob("*.json")}

        manifest = self.store.manifest()

        self.assertEqual([item["id"] for item in manifest["active_papers"]], ["alpha", "beta"])
        self.assertEqual(before, {path: path.read_bytes() for path in self.extracted.glob("*.json")})
        self.assertTrue(str(self.store.state_dir).startswith(str(self.root / "runs")))

    def test_runtime_paper_is_immutable_and_manifest_switch_is_atomic(self):
        upload = paper("uploaded", "uploaded-m1")
        entry = self.store.persist_runtime_paper(upload, "full paper text")
        object_path = self.store.data_path(entry)
        self.assertTrue(object_path.is_file())

        pending = self.store.prepare_transition(
            self.store.active_entries() + [entry], "activate-uploaded"
        )
        self.store.commit_transition(pending)
        self.store.complete_transition(pending)

        self.assertIn("uploaded", [item["id"] for item in self.store.active_entries()])
        self.assertEqual(json.loads(object_path.read_text())["paper"]["id"], "uploaded")

    def test_corrupt_manifest_restores_backup_but_fails_closed(self):
        pending = self.store.prepare_transition([], "new")
        self.store.commit_transition(pending)
        self.store.complete_transition(pending)
        self.store.manifest_path.write_text("{broken", encoding="utf-8")

        with self.assertRaisesRegex(WorkspaceStorageError, "graph reconciliation"):
            self.store.manifest()

        recovered = self.store.manifest(allow_recovery=True)
        self.assertEqual(recovered["revision"], 0)
        self.assertEqual([item["id"] for item in recovered["active_papers"]], ["alpha", "beta"])
        self.assertTrue(self.store.recovery_path.is_file())
        with self.assertRaisesRegex(WorkspaceStorageError, "graph reconciliation"):
            self.store.active_entries()

    def test_manifest_backup_recovery_reconciles_graph_before_unblocking(self):
        pending = self.store.prepare_transition([], "new")
        self.store.commit_transition(pending)
        self.store.complete_transition(pending)
        self.store.manifest_path.write_text("{broken", encoding="utf-8")

        with (
            patch.object(workspace, "get_workspace_store", return_value=self.store),
            patch.object(
                workspace,
                "_replace_graph",
                return_value={"papers": 2, "claims": 0, "methods": 2, "runs": 0},
            ) as replace_graph,
        ):
            result = workspace.recover_workspace()

        self.assertTrue(result["ok"])
        self.assertEqual(result["recovered_operation"], "manifest-recovery-r00000000")
        replace_graph.assert_called_once_with(
            self.store, allow_manifest_recovery=True
        )
        self.assertFalse(self.store.recovery_path.exists())
        self.assertEqual(self.store.manifest()["revision"], 0)
        recovery_log = (
            self.store.archive_dir
            / "manifest-recovery-r00000000"
            / "recovery.json"
        )
        self.assertEqual(json.loads(recovery_log.read_text())["phase"], "complete")

    def test_manifest_backup_recovery_stays_blocked_when_graph_sync_fails(self):
        pending = self.store.prepare_transition([], "new")
        self.store.commit_transition(pending)
        self.store.complete_transition(pending)
        self.store.manifest_path.write_text("{broken", encoding="utf-8")

        with (
            patch.object(workspace, "get_workspace_store", return_value=self.store),
            patch.object(workspace, "_replace_graph", side_effect=RuntimeError("neo4j down")),
        ):
            result = workspace.recover_workspace()

        self.assertTrue(result["partial"])
        self.assertEqual(result["sync"]["state"], "pending_graph_recovery")
        self.assertTrue(self.store.recovery_path.is_file())
        with self.assertRaisesRegex(WorkspaceStorageError, "graph reconciliation"):
            self.store.manifest()

    def test_uncommitted_archive_is_rolled_back_deterministically(self):
        run = self.root / "runs" / "run-alpha.json"
        run.write_text(json.dumps({"method_id": "alpha-m1"}), encoding="utf-8")
        pending = self.store.prepare_transition([], "interrupted")
        self.store.archive_runtime(pending, method_ids={"alpha-m1"})
        self.assertFalse(run.exists())

        result = self.store.recover_storage()

        self.assertEqual(result["phase"], "rolled_back")
        self.assertTrue(run.exists())
        self.assertIsNone(self.store.pending())

    def test_generated_cache_is_archived_by_method_and_restored_in_place(self):
        alpha_cache = self.root / "generated" / "cache" / "alpha-m1" / "digest.py"
        beta_cache = self.root / "generated" / "cache" / "beta-m1" / "digest.py"
        alpha_cache.parent.mkdir(parents=True)
        beta_cache.parent.mkdir(parents=True)
        alpha_cache.write_text("alpha", encoding="utf-8")
        beta_cache.write_text("beta", encoding="utf-8")

        pending = self.store.prepare_transition([], "archive-method-cache")
        moved = self.store.archive_runtime(pending, method_ids={"alpha-m1"})

        self.assertEqual(moved, 1)
        self.assertFalse(alpha_cache.exists())
        self.assertTrue(beta_cache.exists())
        archive_path = Path(self.store.pending()["moved"][0]["archive"])
        self.assertEqual(
            archive_path.relative_to(self.store.archive_dir / pending["operation_id"]),
            Path("generated/cache/alpha-m1/digest.py"),
        )

        self.store.recover_storage()
        self.assertEqual(alpha_cache.read_text(encoding="utf-8"), "alpha")

    def test_archive_move_intent_is_durable_before_rename(self):
        run = self.root / "runs" / "run-alpha.json"
        run.write_text(json.dumps({"method_id": "alpha-m1"}), encoding="utf-8")
        pending = self.store.prepare_transition([], "durable-intent")
        real_replace = os.replace
        observed = {}

        def inspect_replace(source, destination):
            if Path(source) == run:
                observed["move"] = self.store.pending()["moved"][-1]
            return real_replace(source, destination)

        with patch("app.workspace_storage.os.replace", side_effect=inspect_replace):
            self.store.archive_runtime(pending, method_ids={"alpha-m1"})

        self.assertEqual(observed["move"]["source"], str(run))
        self.assertEqual(observed["move"]["state"], "planned")
        self.assertEqual(self.store.pending()["moved"][-1]["state"], "moved")

    def test_recovery_restores_move_after_crash_before_confirmation(self):
        run = self.root / "runs" / "run-alpha.json"
        run.write_text(json.dumps({"method_id": "alpha-m1"}), encoding="utf-8")
        pending = self.store.prepare_transition([], "crash-after-rename")
        real_replace = os.replace

        def crash_after_replace(source, destination):
            result = real_replace(source, destination)
            if Path(source) == run:
                raise RuntimeError("simulated process crash")
            return result

        with (
            patch("app.workspace_storage.os.replace", side_effect=crash_after_replace),
            self.assertRaisesRegex(RuntimeError, "simulated process crash"),
        ):
            self.store.archive_runtime(pending, method_ids={"alpha-m1"})

        journal = self.store.pending()
        self.assertEqual(journal["moved"][-1]["state"], "planned")
        self.assertFalse(run.exists())
        self.assertTrue(Path(journal["moved"][-1]["archive"]).exists())

        recovered = self.store.recover_storage()

        self.assertEqual(recovered["phase"], "rolled_back")
        self.assertTrue(run.exists())
        self.assertIsNone(self.store.pending())

    def test_recovery_keeps_source_after_crash_before_rename(self):
        run = self.root / "runs" / "run-alpha.json"
        run.write_text(json.dumps({"method_id": "alpha-m1"}), encoding="utf-8")
        pending = self.store.prepare_transition([], "crash-before-rename")
        real_replace = os.replace

        def crash_before_replace(source, destination):
            if Path(source) == run:
                raise RuntimeError("simulated process crash")
            return real_replace(source, destination)

        with (
            patch("app.workspace_storage.os.replace", side_effect=crash_before_replace),
            self.assertRaisesRegex(RuntimeError, "simulated process crash"),
        ):
            self.store.archive_runtime(pending, method_ids={"alpha-m1"})

        self.assertEqual(self.store.pending()["moved"][-1]["state"], "planned")
        self.assertTrue(run.exists())
        recovered = self.store.recover_storage()
        self.assertEqual(recovered["phase"], "rolled_back")
        self.assertTrue(run.exists())

    def test_recover_workspace_normalizes_precommit_rollback_result(self):
        pending = self.store.prepare_transition([], "interrupted")
        self.store.archive_runtime(pending, method_ids=None)
        with (
            patch.object(workspace, "get_workspace_store", return_value=self.store),
            patch.object(
                workspace,
                "_replace_graph",
                return_value={"papers": 2, "claims": 0, "methods": 2, "runs": 0},
            ),
        ):
            result = workspace.recover_workspace()
        self.assertTrue(result["ok"])
        self.assertEqual(result["recovery_action"], "storage_rolled_back")
        self.assertEqual(result["papers"], 2)


class WorkspaceTransitionTests(WorkspaceFixture):
    def test_upload_respects_active_paper_quota(self):
        data = paper("third", "third-m1")
        data["paper"]["authors"] = ["Test Author"]
        data["claims"] = [{"id": "third-c1", "text": "A testable claim", "metric": None}]
        with (
            patch.dict(os.environ, {"VERIGRAPH_MAX_ACTIVE_PAPERS": "2"}),
            patch.object(workspace, "get_workspace_store", return_value=self.store),
            patch.object(workspace, "_replace_graph") as replace_graph,
        ):
            with self.assertRaisesRegex(WorkspaceStorageError, "limited to 2 active papers"):
                workspace.persist_paper_and_activate(data, "uploaded paper text")
        replace_graph.assert_not_called()

    def test_upload_helper_persists_and_activates_valid_extraction(self):
        repository = Path(workspace.__file__).resolve().parent.parent
        source = repository / "papers" / "extracted" / "adam2014.json"
        data = json.loads(source.read_text(encoding="utf-8"))
        before = source.read_bytes()
        with (
            patch.object(workspace, "get_workspace_store", return_value=self.store),
            patch.object(
                workspace,
                "_replace_graph",
                return_value={"papers": 3, "claims": 3, "methods": 3, "runs": 0},
            ),
        ):
            result = workspace.persist_paper_and_activate(data, "uploaded paper text")

        self.assertTrue(result["workspace"]["ok"])
        entry = next(item for item in self.store.active_entries() if item["id"] == "adam2014")
        self.assertEqual(entry["source"]["kind"], "runtime")
        self.assertTrue(self.store.data_path(entry).is_file())
        self.assertEqual(source.read_bytes(), before)

    def test_active_method_lookup_uses_manifest_not_impl_files(self):
        with patch.object(workspace, "get_workspace_store", return_value=self.store):
            self.assertEqual(workspace.active_method_ids(), {"alpha-m1", "beta-m1"})
            self.assertEqual(workspace.require_active_method("alpha-m1")["id"], "alpha-m1")
            with workspace.active_method_guard("alpha-m1") as method:
                self.assertEqual(method["id"], "alpha-m1")

            pending = self.store.prepare_transition(
                [{"id": "beta", "source": {"kind": "tracked"}}], "remove-alpha"
            )
            self.store.commit_transition(pending)
            self.store.complete_transition(pending)
            with self.assertRaisesRegex(FileNotFoundError, "not in the active workspace"):
                workspace.require_active_method("alpha-m1")

    def test_new_workspace_preserves_sources_and_reports_graph_failure(self):
        before = {path: path.read_bytes() for path in self.extracted.glob("*.json")}
        with (
            patch.object(workspace, "get_workspace_store", return_value=self.store),
            patch.object(workspace, "_replace_graph", side_effect=RuntimeError("neo4j down")),
        ):
            result = workspace.new_workspace()

        self.assertTrue(result["partial"])
        self.assertEqual(result["sync"]["stage"], "graph")
        self.assertEqual(self.store.active_entries(), [])
        self.assertEqual(before, {path: path.read_bytes() for path in self.extracted.glob("*.json")})
        self.assertEqual(self.store.pending()["phase"], "manifest_committed")

        with (
            patch.object(workspace, "get_workspace_store", return_value=self.store),
            patch.object(
                workspace,
                "_replace_graph",
                return_value={"papers": 0, "claims": 0, "methods": 0, "runs": 0},
            ),
        ):
            recovered = workspace.recover_workspace()
        self.assertTrue(recovered["ok"])
        self.assertIsNone(self.store.pending())

    def test_remove_archives_only_the_removed_papers_runs(self):
        alpha_run = self.root / "runs" / "alpha-run.json"
        beta_run = self.root / "runs" / "beta-run.json"
        alpha_run.write_text(json.dumps({"method_id": "alpha-m1"}), encoding="utf-8")
        beta_run.write_text(json.dumps({"method_id": "beta-m1"}), encoding="utf-8")
        with (
            patch.object(workspace, "get_workspace_store", return_value=self.store),
            patch.object(
                workspace,
                "_replace_graph",
                return_value={"papers": 1, "claims": 0, "methods": 1, "runs": 1},
            ),
        ):
            result = workspace.remove_paper("alpha")

        self.assertTrue(result["ok"])
        self.assertFalse(alpha_run.exists())
        self.assertTrue(beta_run.exists())
        self.assertTrue((self.extracted / "alpha.json").exists())
        self.assertEqual([item["id"] for item in self.store.active_entries()], ["beta"])


class GraphTransactionTests(WorkspaceFixture):
    def test_graph_reset_targets_only_owned_namespace(self):
        class FakeDriver:
            def __init__(self):
                self.nodes = [
                    {"labels": {"Paper"}, "namespace": None},
                    {
                        "labels": {"Paper", GRAPH_OWNER_LABEL},
                        "namespace": "another-app",
                    },
                    {
                        "labels": {"Paper", GRAPH_OWNER_LABEL},
                        "namespace": GRAPH_NAMESPACE,
                    },
                ]
                self.calls = []

            def execute_query(self, cypher, **params):
                self.calls.append((cypher, params))
                namespace = params["graph_namespace"]
                self.nodes = [
                    node
                    for node in self.nodes
                    if not (
                        GRAPH_OWNER_LABEL in node["labels"]
                        and node["namespace"] == namespace
                    )
                ]
                return [], None, None

        driver = FakeDriver()
        graph.reset_our_graph(driver)

        self.assertEqual(len(driver.calls), 1)
        self.assertIn(f"n:{GRAPH_OWNER_LABEL}", driver.calls[0][0])
        self.assertEqual(
            driver.nodes,
            [
                {"labels": {"Paper"}, "namespace": None},
                {
                    "labels": {"Paper", GRAPH_OWNER_LABEL},
                    "namespace": "another-app",
                },
            ],
        )

    def test_graph_replacement_uses_one_mocked_transaction(self):
        class FakeTx:
            def __init__(self):
                self.cypher = []
                self.nodes = [
                    {"name": "foreign-generic", "labels": {"Paper"}, "namespace": None},
                    {
                        "name": "foreign-other-namespace",
                        "labels": {"Paper", GRAPH_OWNER_LABEL},
                        "namespace": "another-app",
                    },
                    {
                        "name": "owned-paper",
                        "labels": {"Paper", GRAPH_OWNER_LABEL},
                        "namespace": GRAPH_NAMESPACE,
                    },
                    {
                        "name": "owned-run",
                        "labels": {"Run", GRAPH_OWNER_LABEL},
                        "namespace": GRAPH_NAMESPACE,
                    },
                ]

            def run(self, cypher, **params):
                self.cypher.append(cypher)
                if "DETACH DELETE" in cypher:
                    namespace = params["graph_namespace"]
                    self.nodes = [
                        node
                        for node in self.nodes
                        if not (
                            GRAPH_OWNER_LABEL in node["labels"]
                            and node["namespace"] == namespace
                        )
                    ]

        class FakeSession:
            def __init__(self):
                self.calls = 0
                self.tx = FakeTx()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute_write(self, callback):
                self.calls += 1
                return callback(self.tx)

        class FakeDriver:
            def __init__(self):
                self.session_instance = FakeSession()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def session(self, **_kwargs):
                return self.session_instance

        driver = FakeDriver()
        with patch.object(workspace, "get_driver", return_value=driver):
            stats = workspace._replace_graph(self.store)

        self.assertEqual(driver.session_instance.calls, 1)
        deletes = [query for query in driver.session_instance.tx.cypher if "DETACH DELETE" in query]
        self.assertEqual(len(deletes), 1)
        self.assertIn(f"n:{GRAPH_OWNER_LABEL}", deletes[0])
        self.assertIn("verigraph_namespace: $graph_namespace", deletes[0])
        self.assertEqual(
            [node["name"] for node in driver.session_instance.tx.nodes],
            ["foreign-generic", "foreign-other-namespace"],
        )
        self.assertEqual(stats["papers"], 2)


if __name__ == "__main__":
    unittest.main()
