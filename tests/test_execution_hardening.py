from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app import codegen, curator, grounded_qa, runner


METHOD_ID = "paper2026-m1"
CLAIM_ID = "paper2026-c1"
PARAMETERS = [
    {
        "name": "steps",
        "default": 10,
        "description": "number of optimization steps",
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
    }
]
IMPLEMENTATION_FINGERPRINT = "a" * 64
CONTEXT_DIGEST = "b" * 64


def method_context(*, description: str = "test method") -> dict:
    extraction = {
        "paper": {"id": "paper2026", "title": "Paper", "year": 2026},
        "claims": [{"id": CLAIM_ID, "text": "claim"}],
        "methods": [
            {
                "id": METHOD_ID,
                "name": "Method",
                "description": description,
                "runnable_hint": "run it",
                "params": PARAMETERS,
            }
        ],
    }
    return {
        **extraction["methods"][0],
        "claims": extraction["claims"],
        "paper": extraction["paper"],
        "extraction": extraction,
        "workspace_source": {"kind": "runtime", "object": "c" * 64},
        "workspace_source_kind": "runtime",
    }


def result(
    *,
    method_id: str = METHOD_ID,
    claim_id: str = CLAIM_ID,
    verdict: str = "VALIDATES",
) -> dict:
    return {
        "method_id": method_id,
        "params": {"steps": 10},
        "metrics": {"score": 0.75},
        "claim_checks": [
            {"claim_id": claim_id, "verdict": verdict, "detail": "score >= 0.5"}
        ],
    }


def record(**changes) -> dict:
    value = {
        "run_id": "run-paper2026-m1-test",
        "method_id": METHOD_ID,
        "implementation_source": "curated",
        "implementation_fingerprint": IMPLEMENTATION_FINGERPRINT,
        "context_digest": CONTEXT_DIGEST,
        "provisional": False,
        "workspace_revision": 2,
        "backend": "local",
        "exit_code": 0,
        "duration_s": 0.1,
        "stdout": json.dumps(result()),
        "stderr": "",
        "created_at": "2026-07-11T10:00:00+00:00",
        "result": result(),
        "error": None,
        "params": {"steps": 10},
    }
    value.update(changes)
    return value


class FakeTx:
    def __init__(
        self,
        *,
        method_exists: bool = True,
        claims: list[str] | None = None,
        existing_hash: str | None = None,
    ):
        self.method_exists = method_exists
        self.claims = claims if claims is not None else [CLAIM_ID]
        self.existing_hash = existing_hash
        self.calls: list[tuple[str, dict]] = []

    def run(self, query: str, **kwargs):
        self.calls.append((query, kwargs))
        if "collect(DISTINCT c.id) AS claim_ids" in query:
            if not self.method_exists:
                return []
            return [{"method_id": METHOD_ID, "claim_ids": self.claims}]
        if "ON CREATE SET r.record_hash" in query:
            return [
                {
                    "record_hash": self.existing_hash or kwargs["record_hash"],
                    "method_ids": [],
                }
            ]
        if "RETURN r.id AS run_id" in query:
            return [{"run_id": kwargs["run_id"]}]
        if "RETURN count(v) AS links" in query:
            return [{"links": 1}]
        return []


def test_result_contract_rejects_method_claim_and_schema_spoofing():
    with pytest.raises(ValueError, match="does not match expected"):
        codegen.validate_result_contract(
            result(method_id="other2026-m1"),
            expected_method_id=METHOD_ID,
            allowed_claim_ids={CLAIM_ID},
        )
    with pytest.raises(ValueError, match="not associated"):
        codegen.validate_result_contract(
            result(claim_id="other2026-c1"),
            expected_method_id=METHOD_ID,
            allowed_claim_ids={CLAIM_ID},
        )
    malformed = result()
    malformed.pop("params")
    with pytest.raises(ValueError, match="missing fields: params"):
        codegen.validate_result_contract(malformed)
    malformed = result()
    malformed["claim_checks"][0]["verdict"] = "SUPPORTS"
    with pytest.raises(ValueError, match="VALIDATES or REFUTES"):
        codegen.validate_result_contract(malformed)
    with pytest.raises(ValueError, match="requested value"):
        codegen.validate_result_contract(result(), expected_params={"steps": 12})
    missing_default = result()
    missing_default["params"] = {}
    with pytest.raises(ValueError, match="missing requested parameter"):
        codegen.validate_result_contract(missing_default, expected_params={"steps": 10})
    oversized_detail = result()
    oversized_detail["claim_checks"][0]["detail"] = "x" * (codegen.MAX_DETAIL_CHARS + 1)
    with pytest.raises(ValueError, match="detail exceeds"):
        codegen.validate_result_contract(oversized_detail)


def test_result_contract_rejects_duplicate_claims_and_non_finite_metrics():
    duplicated = result()
    duplicated["claim_checks"].append(dict(duplicated["claim_checks"][0]))
    with pytest.raises(ValueError, match="duplicate claim"):
        codegen.validate_result_contract(duplicated)
    invalid_number = result()
    invalid_number["metrics"]["score"] = float("nan")
    with pytest.raises(ValueError, match="NaN or infinity"):
        codegen.validate_result_contract(invalid_number)
    stdout = json.dumps(result())[:-1] + ', "extra": NaN}'
    with pytest.raises(ValueError, match="non-finite JSON"):
        codegen.parse_result_line(stdout)
    duplicated_key = json.dumps(result()).replace(
        '"method_id": "paper2026-m1"',
        '"method_id": "paper2026-m1", "method_id": "paper2026-m1"',
    )
    with pytest.raises(ValueError, match="duplicate JSON key"):
        codegen.parse_result_line(duplicated_key)


def test_generated_cache_and_materialization_are_single_writer(tmp_path: Path):
    impl = tmp_path / "impl"
    generated = tmp_path / "generated"
    impl.mkdir()
    generated.mkdir()
    generated_code = "print('complete implementation')\n"

    with (
        patch.object(codegen, "IMPL_DIR", str(impl)),
        patch.object(codegen, "GENERATED_DIR", str(generated)),
        patch.object(codegen, "CACHE_DIR", str(generated / "cache")),
        patch.object(codegen, "LOCK_DIR", str(generated / ".locks")),
        patch.object(codegen, "_method_context", return_value=method_context()),
        patch.object(codegen, "generate_implementation", return_value=generated_code) as generate,
    ):
        with ThreadPoolExecutor(max_workers=8) as pool:
            outputs = list(pool.map(lambda _: codegen.materialize_with_source(METHOD_ID), range(16)))

    assert generate.call_count == 1
    assert {source for _, source in outputs} == {"llm"}
    assert len({path for path, _ in outputs}) == 1
    materialized = Path(outputs[0][0])
    assert ".llm." in materialized.name
    assert materialized.read_text() == generated_code
    digest = codegen._context_digest(method_context())
    assert (generated / "cache" / METHOD_ID / f"{digest}.py").read_text() == generated_code
    assert not list(generated.rglob(".*.tmp"))


def test_curated_materialization_records_trusted_provenance(tmp_path: Path):
    impl = tmp_path / "impl"
    generated = tmp_path / "generated"
    impl.mkdir()
    generated.mkdir()
    (impl / f"{METHOD_ID}.py").write_text("print('curated')\n")
    with (
        patch.object(codegen, "IMPL_DIR", str(impl)),
        patch.object(codegen, "GENERATED_DIR", str(generated)),
        patch.object(codegen, "CACHE_DIR", str(generated / "cache")),
        patch.object(codegen, "LOCK_DIR", str(generated / ".locks")),
        patch.object(codegen, "_method_context", return_value=method_context()),
    ):
        path, source = codegen.materialize_with_source(METHOD_ID)
    assert source == "curated"
    assert ".curated." in Path(path).name


def test_runtime_source_cannot_reuse_same_id_curated_file(tmp_path: Path):
    impl = tmp_path / "impl"
    generated = tmp_path / "generated"
    impl.mkdir()
    generated.mkdir()
    (impl / f"{METHOD_ID}.py").write_text("print('tracked curated')\n")
    runtime_code = "print('runtime generated')\n"
    with (
        patch.object(codegen, "IMPL_DIR", str(impl)),
        patch.object(codegen, "GENERATED_DIR", str(generated)),
        patch.object(codegen, "CACHE_DIR", str(generated / "cache")),
        patch.object(codegen, "LOCK_DIR", str(generated / ".locks")),
        patch.object(codegen, "_method_context", return_value=method_context()),
        patch.object(codegen, "generate_implementation", return_value=runtime_code),
    ):
        path, source = codegen.materialize_with_source(
            METHOD_ID,
            allow_curated=False,
        )
    assert source == "llm"
    assert Path(path).read_text() == runtime_code


def test_generated_cache_is_keyed_by_complete_paper_context(tmp_path: Path):
    generated = tmp_path / "generated"
    generated.mkdir()
    first = method_context(description="revision one")
    second = method_context(description="revision two")
    with (
        patch.object(codegen, "IMPL_DIR", str(tmp_path / "impl")),
        patch.object(codegen, "GENERATED_DIR", str(generated)),
        patch.object(codegen, "CACHE_DIR", str(generated / "cache")),
        patch.object(codegen, "LOCK_DIR", str(generated / ".locks")),
        patch.object(
            codegen,
            "generate_implementation",
            side_effect=["print('one')\n", "print('two')\n"],
        ) as generate,
    ):
        with patch.object(codegen, "_method_context", return_value=first):
            first_path, _, _, first_digest = codegen.materialize_with_provenance(
                METHOD_ID,
                allow_curated=False,
            )
        with patch.object(codegen, "_method_context", return_value=second):
            second_path, _, _, second_digest = codegen.materialize_with_provenance(
                METHOD_ID,
                allow_curated=False,
            )

    assert first_digest != second_digest
    assert first_path != second_path
    assert Path(first_path).read_text() == "print('one')\n"
    assert Path(second_path).read_text() == "print('two')\n"
    assert generate.call_count == 2


def test_legacy_generated_file_is_not_assigned_todays_context_digest(tmp_path: Path):
    impl = tmp_path / "impl"
    generated = tmp_path / "generated"
    impl.mkdir()
    generated.mkdir()
    legacy = f"# {codegen.LEGACY_LLM_MARKER} old context\nprint('stale')\n"
    (impl / f"{METHOD_ID}.py").write_text(legacy)
    with (
        patch.object(codegen, "IMPL_DIR", str(impl)),
        patch.object(codegen, "GENERATED_DIR", str(generated)),
        patch.object(codegen, "CACHE_DIR", str(generated / "cache")),
        patch.object(codegen, "LOCK_DIR", str(generated / ".locks")),
        patch.object(codegen, "_method_context", return_value=method_context()),
        patch.object(
            codegen,
            "generate_implementation",
            return_value="print('current')\n",
        ) as generate,
    ):
        path, source, _, _ = codegen.materialize_with_provenance(METHOD_ID)
    assert source == "llm"
    assert Path(path).read_text() == "print('current')\n"
    generate.assert_called_once()


def test_cache_prune_removes_legacy_and_inactive_context_entries(tmp_path: Path):
    cache = tmp_path / "cache"
    active = cache / METHOD_ID / f"{CONTEXT_DIGEST}.py"
    stale = cache / METHOD_ID / f"{'c' * 64}.py"
    legacy = cache / f"{METHOD_ID}.py"
    active.parent.mkdir(parents=True)
    active.write_text("active")
    stale.write_text("stale")
    legacy.write_text("legacy")
    extraction = {"methods": [{"id": METHOD_ID}]}
    with (
        patch.object(codegen, "CACHE_DIR", str(cache)),
        patch("app.workspace.active_papers", return_value=[extraction]),
        patch.object(codegen, "method_context_digest", return_value=CONTEXT_DIGEST),
    ):
        preview = codegen.prune_implementation_cache(dry_run=True)
        removed = codegen.prune_implementation_cache()
    assert preview == removed
    assert active.is_file()
    assert not stale.exists()
    assert not legacy.exists()


def test_local_environment_does_not_inherit_server_secrets():
    with patch.dict(
        os.environ,
        {
            "PATH": "/usr/bin",
            "DAYTONA_API_KEY": "secret",
            "NEO4J_PASSWORD": "secret",
            "BUTTERBASE_API_KEY": "secret",
        },
        clear=True,
    ):
        env = runner._local_env({"steps": 5}, PARAMETERS)
    assert env["PATH"] == "/usr/bin"
    assert env["P2R_STEPS"] == "5"
    assert "DAYTONA_API_KEY" not in env
    assert "NEO4J_PASSWORD" not in env
    assert "BUTTERBASE_API_KEY" not in env


def test_run_error_text_redacts_configured_credentials():
    secret = "super-secret-value-123"
    with patch.dict(os.environ, {"DAYTONA_API_KEY": secret}, clear=True):
        message = runner._redact_error_text(RuntimeError(f"request failed for {secret}"))
    assert secret not in message
    assert "[redacted]" in message


def test_daytona_sdk_supports_locked_runtime_shape():
    daytona_sdk = pytest.importorskip("daytona_sdk")
    fields = daytona_sdk.CreateSandboxFromImageParams.model_fields
    assert "network_block_all" in fields
    assert "ephemeral" in fields
    image = daytona_sdk.Image.debian_slim("3.12").pip_install("numpy==2.2.6")
    assert "numpy==2.2.6" in image.dockerfile()


@pytest.mark.parametrize("params", [{"steps": True}, {"unknown": 4}, {"STEPS": 4, "steps": 5}])
def test_parameter_environment_rejects_unsafe_direct_values(params):
    with pytest.raises(ValueError):
        runner._param_env(params, PARAMETERS)


def _workspace_mocks():
    store = MagicMock()
    store.manifest.return_value = {"revision": 2}
    return (
        patch("app.workspace.active_method_guard", return_value=nullcontext({"id": METHOD_ID})),
        patch("app.workspace.get_workspace_store", return_value=store),
    )


def test_generated_implementation_without_daytona_never_materializes_or_runs(tmp_path: Path):
    guard, store = _workspace_mocks()
    with (
        guard,
        store,
        patch.object(runner, "RUNS_DIR", str(tmp_path)),
        patch.object(runner, "method_contract", return_value=({CLAIM_ID}, PARAMETERS)),
        patch.object(runner, "method_workspace_source", return_value="runtime"),
        patch.object(
            runner,
            "implementation_metadata",
            return_value=("llm", None, CONTEXT_DIGEST),
        ) as metadata,
        patch.object(runner, "materialize_with_provenance") as materialize,
        patch.object(runner, "run_local") as local,
        patch.object(runner, "run_daytona") as daytona,
        patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True),
    ):
        output = runner.execute(METHOD_ID, "auto", {"steps": 5})
    assert "require DAYTONA_API_KEY" in output["error"]
    materialize.assert_not_called()
    local.assert_not_called()
    daytona.assert_not_called()
    metadata.assert_called_once_with(METHOD_ID, allow_curated=False)
    assert output["implementation_source"] == "llm"
    assert output["implementation_fingerprint"] is None
    assert output["context_digest"] == CONTEXT_DIGEST
    assert output["provisional"] is True


def test_generated_daytona_failure_never_falls_back_local(tmp_path: Path):
    guard, store = _workspace_mocks()
    with (
        guard,
        store,
        patch.object(runner, "RUNS_DIR", str(tmp_path)),
        patch.object(runner, "method_contract", return_value=({CLAIM_ID}, PARAMETERS)),
        patch.object(runner, "method_workspace_source", return_value="runtime"),
        patch.object(
            runner,
            "implementation_metadata",
            return_value=("llm", IMPLEMENTATION_FINGERPRINT, CONTEXT_DIGEST),
        ),
        patch.object(
            runner,
            "materialize_with_provenance",
            return_value=(
                str(tmp_path / "generated.py"),
                "llm",
                IMPLEMENTATION_FINGERPRINT,
                CONTEXT_DIGEST,
            ),
        ),
        patch.object(runner, "run_daytona", side_effect=RuntimeError("sandbox unavailable")),
        patch.object(runner, "run_local") as local,
        patch.dict(os.environ, {"DAYTONA_API_KEY": "test", "PATH": "/usr/bin"}, clear=True),
    ):
        output = runner.execute(METHOD_ID, "auto", {"steps": 5})
    assert "sandbox unavailable" in output["error"]
    local.assert_not_called()


def test_run_record_bounds_untrusted_stdout(tmp_path: Path):
    guard, store = _workspace_mocks()
    oversized = "x" * (runner.MAX_STDOUT_CHARS + 1) + "\n" + json.dumps(result())
    with (
        guard,
        store,
        patch.object(runner, "RUNS_DIR", str(tmp_path)),
        patch.object(runner, "method_contract", return_value=({CLAIM_ID}, PARAMETERS)),
        patch.object(runner, "method_workspace_source", return_value="runtime"),
        patch.object(
            runner,
            "implementation_metadata",
            return_value=("llm", IMPLEMENTATION_FINGERPRINT, CONTEXT_DIGEST),
        ),
        patch.object(
            runner,
            "materialize_with_provenance",
            return_value=(
                str(tmp_path / "generated.py"),
                "llm",
                IMPLEMENTATION_FINGERPRINT,
                CONTEXT_DIGEST,
            ),
        ),
        patch.object(
            runner,
            "run_daytona",
            return_value={
                "backend": "daytona",
                "exit_code": 0,
                "stdout": oversized,
                "stderr": "",
                "duration_s": 0.1,
            },
        ),
        patch.dict(os.environ, {"DAYTONA_API_KEY": "test", "PATH": "/usr/bin"}, clear=True),
    ):
        output = runner.execute(METHOD_ID, "auto", {"steps": 5})
    assert "stdout exceeds" in output["error"]
    assert len(output["stdout"]) == runner.MAX_STDOUT_CHARS
    assert output["result"] is None


def test_run_ids_are_collision_resistant():
    run_ids = {runner._new_run_identity(METHOD_ID)[0] for _ in range(1000)}
    assert len(run_ids) == 1000
    assert all(len(run_id.rsplit("-", 1)[-1]) == 32 for run_id in run_ids)


def test_curator_rejects_missing_method_before_writing_run():
    tx = FakeTx(method_exists=False)
    with pytest.raises(ValueError, match="missing"):
        curator.curate_run(tx, record())
    assert not any("MERGE (r:Run" in query for query, _ in tx.calls)


def test_curator_rejects_generated_local_provenance():
    with pytest.raises(ValueError, match="cannot be curated as local"):
        curator.validate_run_record(
            record(implementation_source="llm", backend="local")
        )


def test_curator_migrates_legacy_result_without_inventing_provenance():
    legacy = record(
        run_id="run-paper2026-m1-20260707T212039Z",
        implementation_source=None,
        workspace_revision=None,
    )
    legacy.pop("implementation_source")
    legacy.pop("implementation_fingerprint")
    legacy.pop("context_digest")
    legacy.pop("provisional")
    legacy.pop("workspace_revision")
    legacy["result"].pop("params")
    legacy["params"] = {"steps": "10"}
    normalized = curator.validate_run_record(legacy)
    assert normalized["result"]["params"] == {"steps": 10}
    assert normalized["implementation_source"] == "unknown"
    assert normalized["implementation_fingerprint"] is None
    assert normalized["context_digest"] is None
    assert normalized["provisional"] is True

    current = record()
    current["result"].pop("params")
    with pytest.raises(ValueError, match="missing fields: params"):
        curator.validate_run_record(current)


def test_unknown_source_is_always_provisional_even_when_false_is_stored():
    legacy = record(
        implementation_source="unknown",
        implementation_fingerprint=None,
        context_digest=None,
        provisional=False,
    )
    normalized = curator.validate_run_record(legacy)
    assert normalized["implementation_source"] == "unknown"
    assert normalized["provisional"] is True


def test_curator_rejects_wrong_claim_before_writing_run():
    tx = FakeTx(claims=["paper2026-c2"])
    with pytest.raises(ValueError, match="not associated"):
        curator.curate_run(tx, record())
    assert not any("MERGE (r:Run" in query for query, _ in tx.calls)


def test_curator_writes_all_validated_evidence_in_one_transaction():
    tx = FakeTx()
    curator.curate_run(tx, record())
    queries = [query for query, _ in tx.calls]
    assert any("MERGE (r:Run" in query for query in queries)
    assert any("[v:VALIDATES]" in query for query in queries)
    assert any("MERGE (r:Run:Verigraph" in query for query in queries)
    assert any("MERGE (a:Artifact:Verigraph" in query for query in queries)
    assert all(
        kwargs.get("graph_namespace")
        for query, kwargs in tx.calls
        if any(label in query for label in (":Method", ":Claim", ":Run", ":Artifact"))
    )


def test_curator_rejects_same_run_id_with_different_payload():
    original = record()
    changed = record()
    changed["result"]["claim_checks"][0]["verdict"] = "REFUTES"
    tx = FakeTx(existing_hash=curator._record_fingerprint(original))
    with pytest.raises(ValueError, match="different record"):
        curator.curate_run(tx, changed)
    assert not any("SET r.method_id" in query for query, _ in tx.calls)


def test_latest_run_skips_newer_failure_and_uses_created_at():
    ctx = grounded_qa.WorkspaceCtx(
        runs=[
            {
                "id": "run-z-failed",
                "method_id": METHOD_ID,
                "status": "failure",
                "created_at": "2026-07-11T12:00:00+00:00",
            },
            {
                "id": "run-a-success",
                "method_id": METHOD_ID,
                "status": "success",
                "created_at": "2026-07-11T11:00:00+00:00",
            },
            {
                "id": "run-z-older",
                "method_id": METHOD_ID,
                "status": "success",
                "created_at": "2026-07-11T10:00:00+00:00",
            },
        ]
    )
    assert grounded_qa._latest_run_for_method(ctx, METHOD_ID)["id"] == "run-a-success"


def test_brief_uses_selected_run_metrics_instead_of_hardcoded_values():
    evidence = {
        "verdict": "VALIDATES",
        "runId": "run-current",
        "detail": "measured",
        "implementationSource": "llm",
    }
    claim = grounded_qa.ClaimRow(
        id="wilson2017-c1",
        text="Adaptive methods generalize worse",
        paper_id="wilson2017",
        paper_title="Wilson",
        evidence=evidence,
    )
    ctx = grounded_qa.WorkspaceCtx(
        claims=[claim],
        claim_by_id={claim.id: claim},
        runs=[
            {
                "id": "run-stale",
                "method_id": "wilson2017-m1",
                "status": "success",
                "created_at": "2026-07-10T00:00:00+00:00",
                "metrics": {"test_error_gd": 0.0, "test_error_adam": 0.425},
            },
            {
                "id": "run-current",
                "method_id": "wilson2017-m1",
                "status": "success",
                "implementation_source": "llm",
                "created_at": "2026-07-11T00:00:00+00:00",
                "metrics": {"test_error_gd": 0.125, "test_error_adam": 0.731},
            },
        ],
    )
    brief = grounded_qa.answer_brief(ctx)
    assert "0.125" in brief["payload"]["headline"]
    assert "0.731" in brief["payload"]["headline"]
    assert "0.425" not in brief["payload"]["headline"]
    assert "Provisional LLM-generated" in brief["payload"]["headline"]
    assert brief["payload"]["run_ids_covered"] == ["run-current"]
    assert brief["payload"]["provisional"] == ["wilson2017-c1"]


def test_conductor_reports_failed_run_as_failure():
    ctx = grounded_qa.WorkspaceCtx(
        methods=[{"id": METHOD_ID, "has_run": False}],
    )
    failed = {
        "run_id": "run-failed",
        "method_id": METHOD_ID,
        "backend": "daytona",
        "duration_s": None,
        "result": None,
        "error": "sandbox unavailable",
    }
    with (
        patch.object(grounded_qa, "_load_workspace", return_value=ctx),
        patch("app.workspace.active_method_guard", return_value=nullcontext({"id": METHOD_ID})),
        patch("app.runner.execute", return_value=failed),
        patch("app.curator.curate") as curate,
    ):
        output = grounded_qa.answer_conduct()
    curate.assert_called_once_with(failed)
    assert any(step.startswith("[executor] ✗") for step in output["steps"])
    assert not any(step.startswith("[executor] ✓") for step in output["steps"])


def test_generic_llm_metric_headline_is_provisional():
    evidence = {
        "verdict": "REFUTES",
        "runId": "run-generic",
        "detail": "measured",
        "implementationSource": "llm",
    }
    claim = grounded_qa.ClaimRow(
        id=CLAIM_ID,
        text="A generic claim",
        paper_id="paper2026",
        paper_title="Paper",
        evidence=evidence,
    )
    ctx = grounded_qa.WorkspaceCtx(
        claims=[claim],
        runs=[
            {
                "id": "run-generic",
                "method_id": METHOD_ID,
                "status": "success",
                "implementation_source": "llm",
                "created_at": "2026-07-11T00:00:00+00:00",
                "metrics": {"score": 0.75},
            }
        ],
    )
    headline = grounded_qa.answer_brief(ctx)["payload"]["headline"]
    assert headline.startswith("Provisional LLM-generated")
