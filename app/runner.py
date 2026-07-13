"""Execute implementations with provenance-aware isolation.

LLM-generated implementations can run only in a network-blocked Daytona
sandbox. Curated implementations may run locally, but receive a minimal
allowlisted environment rather than the server process environment.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import certifi

# Must be configured before the Daytona SDK opens TLS connections.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.codegen import (
    implementation_metadata,
    materialize_with_provenance,
    method_contract,
    method_workspace_source,
    parse_result_line,
    validate_method_id,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(ROOT, "runs")
LOCAL_ENV_ALLOWLIST = (
    "LANG",
    "LC_ALL",
    "PATH",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "WINDIR",
)
DAYTONA_NUMPY_VERSION = "2.2.6"
MAX_STDOUT_CHARS = 256_000
MAX_STDERR_CHARS = 128_000
LOCAL_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
LOCAL_OUTPUT_BYTES = max(MAX_STDOUT_CHARS, MAX_STDERR_CHARS)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))


def _redact_error_text(error: Exception) -> str:
    """Keep run diagnostics useful without persisting configured credentials."""
    message = str(error)
    sensitive_markers = ("KEY", "TOKEN", "PASSWORD", "SECRET", "CREDENTIAL")
    for name, value in os.environ.items():
        if (
            value
            and len(value) >= 8
            and any(marker in name.upper() for marker in sensitive_markers)
        ):
            message = message.replace(value, "[redacted]")
    return message[:MAX_STDERR_CHARS]


def _param_env(
    params: dict[str, Any] | None,
    parameter_definitions: list[dict[str, Any]] | str | None = None,
) -> dict[str, str]:
    """Convert validated experiment parameters to the P2R environment."""
    from app.validation import parameter_env

    if parameter_definitions == [] and params:
        raise ValueError("method does not define any experiment parameters")
    return parameter_env(params, parameter_definitions)


def _local_env(
    params: dict[str, Any] | None,
    parameter_definitions: list[dict[str, Any]] | str | None = None,
) -> dict[str, str]:
    env = {name: os.environ[name] for name in LOCAL_ENV_ALLOWLIST if name in os.environ}
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
        }
    )
    env.update(_param_env(params, parameter_definitions))
    return env


def run_local(
    code_path: str,
    timeout: int = 300,
    params: dict[str, Any] | None = None,
    parameter_definitions: list[dict[str, Any]] | str | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    launcher = os.path.join(ROOT, "app", "local_exec.py")
    with tempfile.TemporaryDirectory(prefix="verigraph-local-") as directory:
        stdout_path = os.path.join(directory, "stdout")
        stderr_path = os.path.join(directory, "stderr")
        timed_out = False
        with open(stdout_path, "w+b") as stdout_file, open(stderr_path, "w+b") as stderr_file:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    launcher,
                    os.path.realpath(code_path),
                    str(max(1, int(timeout))),
                    str(LOCAL_MEMORY_BYTES),
                    str(LOCAL_OUTPUT_BYTES),
                ],
                stdout=stdout_file,
                stderr=stderr_file,
                env=_local_env(params, parameter_definitions),
                start_new_session=True,
            )
            try:
                proc.wait(timeout=timeout + 5)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (AttributeError, OSError):
                    proc.kill()
                proc.wait()
            # The implementation may have spawned background children and
            # exited first. Tear down its isolated process group either way.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (AttributeError, ProcessLookupError, OSError):
                pass
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read(MAX_STDOUT_CHARS + 1).decode(
                "utf-8", errors="replace"
            )
            stderr = stderr_file.read(MAX_STDERR_CHARS + 1).decode(
                "utf-8", errors="replace"
            )
        if timed_out:
            stderr = (stderr + f"\nlocal execution exceeded {timeout} seconds").strip()
    return {
        "backend": "local",
        "exit_code": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "duration_s": round(time.monotonic() - started, 2),
    }


def run_daytona(
    code_path: str,
    timeout: int = 300,
    params: dict[str, Any] | None = None,
    run_id: str = "run",
    parameter_definitions: list[dict[str, Any]] | str | None = None,
) -> dict[str, Any]:
    from daytona_sdk import (
        CreateSandboxFromImageParams,
        Daytona,
        DaytonaConfig,
        Image,
    )

    with open(code_path, encoding="utf-8") as handle:
        code = handle.read()

    daytona = Daytona(DaytonaConfig(api_key=os.environ["DAYTONA_API_KEY"]))
    started = time.monotonic()
    sandbox = None
    sandbox_id = None
    cleanup_error = None
    try:
        # Dependencies are built into the image. Runtime networking is fully
        # blocked, and no host secrets or volumes are mounted into the sandbox.
        image = Image.debian_slim("3.12").pip_install(
            f"numpy=={DAYTONA_NUMPY_VERSION}"
        )
        sandbox = daytona.create(
            CreateSandboxFromImageParams(
                name=f"vg-{run_id[-32:]}",
                image=image,
                labels={"app": "verigraph", "run_id": run_id[-63:]},
                public=False,
                network_block_all=True,
                ephemeral=True,
                auto_stop_interval=5,
            ),
            timeout=180,
        )
        sandbox_id = getattr(sandbox, "id", None)
        suffix = uuid.uuid4().hex
        code_remote = f"/tmp/verigraph-{suffix}.py"
        stdout_remote = f"/tmp/verigraph-{suffix}.stdout"
        stderr_remote = f"/tmp/verigraph-{suffix}.stderr"
        exit_remote = f"/tmp/verigraph-{suffix}.exit"
        sandbox.fs.upload_file(code.encode("utf-8"), code_remote)
        file_blocks = max(MAX_STDOUT_CHARS, MAX_STDERR_CHARS) // 512
        # Redirect inside the sandbox and set a hard file-size ceiling before
        # starting untrusted code. This bounds output before the SDK/API ever
        # materializes an execution response in the server process.
        command = (
            "bash -lc '"
            f"ulimit -f {file_blocks}; set +e; "
            f"timeout --signal=KILL {int(timeout)}s python {code_remote} "
            f"> {stdout_remote} 2> {stderr_remote}; "
            f"printf \"%s\" \"$?\" > {exit_remote}'"
        )
        sandbox.process.exec(
            command,
            env=_param_env(params, parameter_definitions),
            timeout=timeout + 15,
        )
        stdout = sandbox.fs.download_file(stdout_remote).decode("utf-8", errors="replace")
        stderr = sandbox.fs.download_file(stderr_remote).decode("utf-8", errors="replace")
        exit_code = int(sandbox.fs.download_file(exit_remote).decode("ascii").strip())
        outcome = {
            "backend": "daytona",
            "sandbox_id": sandbox_id,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "duration_s": round(time.monotonic() - started, 2),
        }
    finally:
        if sandbox is not None:
            try:
                daytona.delete(sandbox, timeout=60)
            except Exception as exc:  # Ephemeral auto-delete remains the backstop.
                cleanup_error = f"{type(exc).__name__}: {str(exc)[:200]}"
    if cleanup_error:
        outcome["cleanup_error"] = cleanup_error
    return outcome


def _new_run_identity(method_id: str) -> tuple[str, str]:
    created_at = datetime.now(timezone.utc)
    timestamp = created_at.strftime("%Y%m%dT%H%M%S.%fZ")
    run_id = f"run-{method_id}-{timestamp}-{uuid.uuid4().hex}"
    return run_id, created_at.isoformat()


def _write_record(record: dict[str, Any]) -> str:
    os.makedirs(RUNS_DIR, exist_ok=True)
    path = os.path.join(RUNS_DIR, f"{record['run_id']}.json")
    fd, temporary = tempfile.mkstemp(prefix=f".{record['run_id']}.", dir=RUNS_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(RUNS_DIR, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def _execute_active(
    method_id: str,
    backend: str,
    params: dict[str, Any] | None,
) -> dict[str, Any]:
    # Querying the method contract first preserves the existing 404 behavior
    # and supplies the allowlist used to validate evidence claims.
    from app.validation import normalize_parameter_overrides
    from app.workspace import get_workspace_store

    allowed_claims, parameter_definitions = method_contract(method_id)
    normalized_params = normalize_parameter_overrides(params, parameter_definitions)
    if parameter_definitions == [] and normalized_params:
        raise ValueError("method does not define any experiment parameters")
    effective_params = {
        definition["name"]: definition["default"]
        for definition in parameter_definitions
    }
    effective_params.update(normalized_params)
    workspace_source = method_workspace_source(method_id)
    allow_curated = workspace_source in {"tracked", "bundled"}
    source, fingerprint, context_digest = implementation_metadata(
        method_id,
        allow_curated=allow_curated,
    )
    requested_backend = backend
    if source == "llm":
        selected_backend = "daytona"
    elif backend == "auto":
        selected_backend = "daytona" if os.environ.get("DAYTONA_API_KEY") else "local"
    else:
        selected_backend = backend

    run_id, created_at = _new_run_identity(method_id)
    record: dict[str, Any] = {
        "run_id": run_id,
        "method_id": method_id,
        "implementation_source": source,
        "implementation_fingerprint": fingerprint,
        "context_digest": context_digest,
        "provisional": source != "curated" or fingerprint is None,
        "workspace_revision": get_workspace_store().manifest()["revision"],
        "backend": selected_backend,
        "params": effective_params,
        "parameter_overrides": normalized_params,
        "created_at": created_at,
        "result": None,
        "error": None,
    }
    try:
        if source == "llm" and requested_backend == "local":
            raise RuntimeError("LLM-generated implementations cannot execute locally")
        if source == "llm" and not os.environ.get("DAYTONA_API_KEY"):
            raise RuntimeError(
                "LLM-generated implementations require DAYTONA_API_KEY and cannot fall back locally"
            )

        (
            code_path,
            materialized_source,
            materialized_fingerprint,
            materialized_context_digest,
        ) = materialize_with_provenance(
            method_id,
            allow_curated=allow_curated,
        )
        if materialized_source != source:
            raise RuntimeError("implementation provenance changed during materialization")
        if materialized_context_digest != context_digest:
            raise RuntimeError("method context changed during materialization")
        if fingerprint is not None and materialized_fingerprint != fingerprint:
            raise RuntimeError("implementation changed during materialization")
        record["implementation_fingerprint"] = materialized_fingerprint
        record["context_digest"] = materialized_context_digest
        record["provisional"] = materialized_source != "curated"

        if selected_backend == "daytona":
            try:
                outcome = run_daytona(
                    code_path,
                    params=normalized_params,
                    run_id=run_id,
                    parameter_definitions=parameter_definitions,
                )
            except Exception as exc:
                if source != "curated" or requested_backend != "auto":
                    raise
                outcome = run_local(
                    code_path,
                    params=normalized_params,
                    parameter_definitions=parameter_definitions,
                )
                outcome["fallback_reason"] = (
                    f"Daytona unavailable for curated code: {type(exc).__name__}"
                )
        else:
            # selected_backend can be local only for a curated implementation.
            outcome = run_local(
                code_path,
                params=normalized_params,
                parameter_definitions=parameter_definitions,
            )

        contract_stdout = outcome.get("stdout") or ""
        stdout_too_large = len(contract_stdout) > MAX_STDOUT_CHARS
        outcome["stdout"] = contract_stdout[:MAX_STDOUT_CHARS]
        outcome["stderr"] = (outcome.get("stderr") or "")[:MAX_STDERR_CHARS]
        record.update(outcome)
        if stdout_too_large:
            record["error"] = (
                f"output contract violation: stdout exceeds {MAX_STDOUT_CHARS} characters"
            )
        elif record["exit_code"] != 0:
            record["error"] = f"non-zero exit ({record['exit_code']})"
        else:
            try:
                record["result"] = parse_result_line(
                    contract_stdout,
                    expected_method_id=method_id,
                    expected_params=effective_params,
                    allowed_claim_ids=allowed_claims,
                )
            except (ValueError, json.JSONDecodeError) as exc:
                record["error"] = f"output contract violation: {exc}"
    except Exception as exc:
        message = _redact_error_text(exc)
        record["backend"] = selected_backend
        record["exit_code"] = -1
        record.setdefault("stdout", "")
        record["stderr"] = message
        record["duration_s"] = None
        record["error"] = f"{type(exc).__name__}: {message}"

    path = _write_record(record)
    print(f"run record: {path}")
    return record


def execute(
    method_id: str,
    backend: str = "auto",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute an active method and persist an immutable, validated record."""
    validate_method_id(method_id)
    if backend not in {"auto", "local", "daytona"}:
        raise ValueError("backend must be auto, local, or daytona")
    from app.workspace import active_method_guard

    try:
        with active_method_guard(method_id):
            return _execute_active(method_id, backend, params)
    except FileNotFoundError as exc:
        raise NotImplementedError(str(exc)) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("method_id", help="e.g. wilson2017-m1")
    parser.add_argument("--backend", choices=["auto", "local", "daytona"], default="auto")
    args = parser.parse_args()

    record = execute(args.method_id, args.backend)
    status = "OK" if record["error"] is None else f"FAILED ({record['error']})"
    print(
        f"{record['run_id']} [{record['backend']}] {status} "
        f"in {record['duration_s']}s"
    )
    if record["result"]:
        for check in record["result"]["claim_checks"]:
            print(f"  {check['verdict']} {check['claim_id']}: {check['detail']}")
    return 0 if record["error"] is None else 1


if __name__ == "__main__":
    sys.exit(main())
