"""Durable, non-destructive storage for the active workspace.

Checked-in paper assets are immutable inputs.  Workspace state lives below
``runs/.workspace`` (``runs`` is ignored by git) and consists of an atomically
replaced manifest plus immutable objects for uploaded papers.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Iterator

try:  # pragma: no cover - Windows fallback is exercised only off Unix.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


ROOT = Path(__file__).resolve().parent.parent
PAPERS_DIR = ROOT / "papers"
EXTRACTED_DIR = PAPERS_DIR / "extracted"
BUNDLED_DIR = PAPERS_DIR / "bundled"
RUNS_DIR = ROOT / "runs"
GENERATED_DIR = ROOT / "generated"
STATE_DIR = RUNS_DIR / ".workspace"

MANIFEST_VERSION = 1
PAPER_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")

_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_LOCK_DEPTH = threading.local()


class WorkspaceStorageError(RuntimeError):
    """Raised when durable workspace state cannot be validated or recovered."""


def validate_paper_id(paper_id: str) -> str:
    if not isinstance(paper_id, str) or not PAPER_ID_RE.fullmatch(paper_id):
        raise ValueError(
            "paper id must be 1-128 ASCII letters, digits, dots, underscores, or hyphens"
        )
    if paper_id in {".", ".."}:
        raise ValueError("paper id cannot be a path component")
    return paper_id


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class WorkspaceStore:
    """Own the active-paper manifest and recoverable runtime archives."""

    def __init__(
        self,
        *,
        root: Path | str = ROOT,
        state_dir: Path | str | None = None,
        extracted_dir: Path | str | None = None,
        bundled_dir: Path | str | None = None,
        runs_dir: Path | str | None = None,
        generated_dir: Path | str | None = None,
    ) -> None:
        self.root = Path(root)
        self.papers_dir = self.root / "papers"
        self.extracted_dir = Path(extracted_dir or self.papers_dir / "extracted")
        self.bundled_dir = Path(bundled_dir or self.papers_dir / "bundled")
        self.runs_dir = Path(runs_dir or self.root / "runs")
        self.generated_dir = Path(generated_dir or self.root / "generated")
        self.state_dir = Path(state_dir or self.runs_dir / ".workspace")
        self.manifest_path = self.state_dir / "manifest.json"
        self.backup_path = self.state_dir / "manifest.json.bak"
        self.recovery_path = self.state_dir / "manifest-recovery.json"
        self.pending_path = self.state_dir / "pending.json"
        self.objects_dir = self.state_dir / "objects"
        self.archive_dir = self.state_dir / "archive"
        self.lock_path = self.state_dir / "workspace.lock"

    def _ensure_dirs(self) -> None:
        for path in (self.state_dir, self.objects_dir, self.archive_dir):
            path.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        """Serialize transitions across API workers and CLI recovery commands."""
        self._ensure_dirs()
        key = str(self.lock_path.resolve())
        with _PROCESS_LOCKS_GUARD:
            process_lock = _PROCESS_LOCKS.setdefault(key, threading.RLock())
        with process_lock:
            depths = getattr(_LOCK_DEPTH, "paths", {})
            depth = depths.get(key, 0)
            if depth:
                depths[key] = depth + 1
                _LOCK_DEPTH.paths = depths
                try:
                    yield
                finally:
                    depths[key] -= 1
                return

            depths[key] = 1
            _LOCK_DEPTH.paths = depths
            lock_file = self.lock_path.open("a+")
            try:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                lock_file.close()
                depths.pop(key, None)

    def _atomic_write_bytes(self, path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            _fsync_dir(path.parent)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def _atomic_write_json(self, path: Path, value: dict) -> None:
        payload = json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        self._atomic_write_bytes(path, payload)

    @staticmethod
    def _read_json(path: Path) -> dict:
        try:
            with path.open(encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceStorageError(f"cannot read workspace state {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise WorkspaceStorageError(f"workspace state must be a JSON object: {path}")
        return value

    def _default_entries(self) -> list[dict]:
        if not self.extracted_dir.is_dir():
            return []
        return [
            {"id": path.stem, "source": {"kind": "tracked"}}
            for path in sorted(self.extracted_dir.glob("*.json"))
            if PAPER_ID_RE.fullmatch(path.stem)
        ]

    def _validate_manifest(self, manifest: dict) -> dict:
        if manifest.get("version") != MANIFEST_VERSION:
            raise WorkspaceStorageError("unsupported workspace manifest version")
        revision = manifest.get("revision")
        if not isinstance(revision, int) or revision < 0:
            raise WorkspaceStorageError("workspace manifest revision must be non-negative")
        entries = manifest.get("active_papers")
        if not isinstance(entries, list):
            raise WorkspaceStorageError("workspace manifest active_papers must be a list")
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise WorkspaceStorageError("workspace paper entry must be an object")
            try:
                paper_id = validate_paper_id(entry.get("id"))
            except (TypeError, ValueError) as exc:
                raise WorkspaceStorageError(f"invalid workspace paper entry: {entry!r}") from exc
            if paper_id in seen:
                raise WorkspaceStorageError(f"duplicate active paper id: {paper_id}")
            seen.add(paper_id)
            source = entry.get("source")
            if not isinstance(source, dict) or source.get("kind") not in {
                "tracked",
                "bundled",
                "runtime",
            }:
                raise WorkspaceStorageError(f"invalid source for paper {paper_id}")
            if source["kind"] == "runtime":
                object_id = source.get("object")
                if not isinstance(object_id, str) or not re.fullmatch(r"[0-9a-f]{64}", object_id):
                    raise WorkspaceStorageError(f"invalid runtime object for paper {paper_id}")
        return manifest

    def manifest(self, *, allow_recovery: bool = False) -> dict:
        """Read the manifest, failing closed while graph reconciliation is pending."""
        self._ensure_dirs()
        if self.recovery_path.exists() and not allow_recovery:
            raise WorkspaceStorageError(
                "workspace manifest was restored from backup; graph reconciliation "
                "is required via `python3 -m app.restore`"
            )
        if not self.manifest_path.exists():
            if self.recovery_path.exists():
                if not self.backup_path.exists():
                    raise WorkspaceStorageError(
                        "workspace manifest recovery is pending but its backup is missing"
                    )
                backup = self._validate_manifest(self._read_json(self.backup_path))
                self._atomic_write_json(self.manifest_path, backup)
                return backup
            initial = {
                "version": MANIFEST_VERSION,
                "revision": 0,
                "active_papers": self._default_entries(),
            }
            self._atomic_write_json(self.manifest_path, initial)
            return initial
        try:
            return self._validate_manifest(self._read_json(self.manifest_path))
        except WorkspaceStorageError as primary_error:
            if not self.backup_path.exists():
                raise
            try:
                backup = self._validate_manifest(self._read_json(self.backup_path))
            except WorkspaceStorageError:
                raise primary_error
            marker = {
                "version": MANIFEST_VERSION,
                "operation_id": f"manifest-recovery-r{backup['revision']:08d}",
                "phase": "pending_graph_reconciliation",
                "recovered_revision": backup["revision"],
                "requires_graph_reconciliation": True,
            }
            # Journal recovery before replacing the corrupt manifest. A crash at
            # either write leaves an explicit, durable reconciliation signal.
            self._atomic_write_json(self.recovery_path, marker)
            self._atomic_write_json(self.manifest_path, backup)
            if not allow_recovery:
                raise WorkspaceStorageError(
                    "workspace manifest was restored from backup; graph reconciliation "
                    "is required via `python3 -m app.restore`"
                ) from primary_error
            return backup

    def manifest_recovery(self) -> dict | None:
        if not self.recovery_path.exists():
            return None
        marker = self._read_json(self.recovery_path)
        if (
            marker.get("version") != MANIFEST_VERSION
            or marker.get("phase") != "pending_graph_reconciliation"
            or marker.get("requires_graph_reconciliation") is not True
            or not isinstance(marker.get("operation_id"), str)
            or not isinstance(marker.get("recovered_revision"), int)
        ):
            raise WorkspaceStorageError("invalid workspace manifest recovery marker")
        return marker

    def complete_manifest_recovery(self, marker: dict) -> None:
        """Record successful graph reconciliation and unblock manifest readers."""
        current = self.manifest_recovery()
        if current is None or current.get("operation_id") != marker.get("operation_id"):
            raise WorkspaceStorageError("manifest recovery marker changed during recovery")
        completed = {**current, "phase": "complete"}
        archive = self.archive_dir / current["operation_id"]
        archive.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(archive / "recovery.json", completed)
        self.recovery_path.unlink()
        _fsync_dir(self.state_dir)

    def active_entries(self, *, allow_manifest_recovery: bool = False) -> list[dict]:
        return list(
            self.manifest(allow_recovery=allow_manifest_recovery)["active_papers"]
        )

    def bundled_entries(self, paper_ids: tuple[str, ...] | None = None) -> list[dict]:
        extracted = self.bundled_dir / "extracted"
        if not extracted.is_dir():
            raise FileNotFoundError(f"bundled papers missing: {extracted}")
        allowed = set(paper_ids) if paper_ids is not None else None
        entries = []
        for path in sorted(extracted.glob("*.json")):
            paper_id = validate_paper_id(path.stem)
            if allowed is None or paper_id in allowed:
                entries.append({"id": paper_id, "source": {"kind": "bundled"}})
        if allowed is not None:
            missing = sorted(allowed - {entry["id"] for entry in entries})
            if missing:
                raise FileNotFoundError(f"bundled papers missing: {', '.join(missing)}")
        return entries

    def data_path(self, entry: dict) -> Path:
        paper_id = validate_paper_id(entry["id"])
        source = entry["source"]
        if source["kind"] == "tracked":
            return self.extracted_dir / f"{paper_id}.json"
        if source["kind"] == "bundled":
            return self.bundled_dir / "extracted" / f"{paper_id}.json"
        return self.objects_dir / source["object"] / "extracted.json"

    def text_path(self, entry: dict) -> Path | None:
        paper_id = validate_paper_id(entry["id"])
        source = entry["source"]
        if source["kind"] == "tracked":
            path = self.papers_dir / f"{paper_id}.txt"
        elif source["kind"] == "bundled":
            path = self.bundled_dir / "texts" / f"{paper_id}.txt"
        else:
            path = self.objects_dir / source["object"] / "paper.txt"
        return path if path.is_file() else None

    def read_paper(self, entry: dict) -> dict:
        path = self.data_path(entry)
        try:
            with path.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceStorageError(f"cannot read active paper {entry['id']}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("paper"), dict):
            raise WorkspaceStorageError(f"invalid extracted paper: {path}")
        if data["paper"].get("id") != entry["id"]:
            raise WorkspaceStorageError(
                f"paper id mismatch in {path}: expected {entry['id']!r}"
            )
        return data

    def active_papers(self, *, allow_manifest_recovery: bool = False) -> list[dict]:
        return [
            self.read_paper(entry)
            for entry in self.active_entries(
                allow_manifest_recovery=allow_manifest_recovery
            )
        ]

    def persist_runtime_paper(self, data: dict, text: str) -> dict:
        """Persist an immutable upload object and return its manifest entry."""
        if not isinstance(data, dict) or not isinstance(data.get("paper"), dict):
            raise ValueError("extraction must contain a paper object")
        paper_id = validate_paper_id(data["paper"].get("id"))
        if not isinstance(text, str):
            raise TypeError("paper text must be a string")
        encoded_data = json.dumps(data, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        encoded_text = text.encode("utf-8")
        digest = hashlib.sha256(encoded_data + b"\0" + encoded_text).hexdigest()
        destination = self.objects_dir / digest
        self._ensure_dirs()
        if not destination.is_dir():
            staging = Path(tempfile.mkdtemp(prefix=".object-", dir=self.objects_dir))
            try:
                self._atomic_write_bytes(staging / "extracted.json", encoded_data)
                self._atomic_write_bytes(staging / "paper.txt", encoded_text)
                try:
                    os.replace(staging, destination)
                except OSError:
                    if not destination.is_dir():
                        raise
                _fsync_dir(self.objects_dir)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        return {"id": paper_id, "source": {"kind": "runtime", "object": digest}}

    def _operation_id(self, revision: int, operation: str) -> str:
        slug = re.sub(r"[^a-z0-9-]+", "-", operation.lower()).strip("-") or "workspace"
        return f"{revision:08d}-{slug}"

    def pending(self) -> dict | None:
        if not self.pending_path.exists():
            return None
        return self._read_json(self.pending_path)

    def prepare_transition(self, entries: list[dict], operation: str) -> dict:
        """Journal a target manifest without changing the active revision."""
        if self.pending_path.exists():
            pending = self.pending()
            raise WorkspaceStorageError(
                f"workspace transition already pending: {pending.get('operation_id')}"
            )
        current = self.manifest()
        target = {
            "version": MANIFEST_VERSION,
            "revision": current["revision"] + 1,
            "active_papers": sorted(entries, key=lambda entry: entry["id"]),
        }
        self._validate_manifest(target)
        operation_id = self._operation_id(target["revision"], operation)
        archive = self.archive_dir / operation_id
        if archive.exists():
            suffix = 2
            while (self.archive_dir / f"{operation_id}-{suffix}").exists():
                suffix += 1
            operation_id = f"{operation_id}-{suffix}"
            archive = self.archive_dir / operation_id
        archive.mkdir(parents=True)
        self._atomic_write_json(archive / "manifest.before.json", current)
        pending = {
            "operation_id": operation_id,
            "operation": operation,
            "phase": "prepared",
            "from_revision": current["revision"],
            "target_manifest": target,
            "moved": [],
        }
        self._atomic_write_json(self.pending_path, pending)
        return pending

    def _update_pending(self, pending: dict) -> None:
        self._atomic_write_json(self.pending_path, pending)

    def archive_runtime(
        self,
        pending: dict,
        *,
        method_ids: set[str] | None = None,
        include_generated: bool = True,
    ) -> int:
        """Move selected runtime artifacts into the operation's recovery archive."""
        pending["phase"] = "archiving"
        self._update_pending(pending)
        candidates: list[Path] = []
        if self.runs_dir.is_dir():
            for path in sorted(self.runs_dir.glob("*.json")):
                selected = method_ids is None
                if method_ids is not None:
                    try:
                        with path.open(encoding="utf-8") as handle:
                            selected = json.load(handle).get("method_id") in method_ids
                    except (OSError, json.JSONDecodeError, AttributeError):
                        # A malformed legacy run cannot be safely attributed or
                        # restored, so quarantine it in the recoverable archive.
                        selected = True
                if selected:
                    candidates.append(path)
        if include_generated and self.generated_dir.is_dir():
            for path in sorted(item for item in self.generated_dir.iterdir() if item.is_file()):
                selected = method_ids is None or any(
                    path.name == method_id
                    or path.name.startswith(f"{method_id}-")
                    or path.name.startswith(f"{method_id}.")
                    for method_id in method_ids
                )
                if selected:
                    candidates.append(path)
            cache_dir = self.generated_dir / "cache"
            if cache_dir.is_dir():
                for path in sorted(item for item in cache_dir.rglob("*") if item.is_file()):
                    relative = path.relative_to(cache_dir)
                    cache_method_id = relative.parts[0] if relative.parts else ""
                    if method_ids is None or cache_method_id in method_ids:
                        candidates.append(path)

        archive = self.archive_dir / pending["operation_id"]
        for source in candidates:
            if source.parent == self.runs_dir:
                destination = archive / "runs" / source.name
            else:
                destination = archive / "generated" / source.relative_to(
                    self.generated_dir
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            move = {
                "source": str(source),
                "archive": str(destination),
                "state": "planned",
            }
            # The intent must reach durable storage before the rename. Recovery
            # can then distinguish both possible crash points around os.replace.
            pending["moved"].append(move)
            self._update_pending(pending)
            os.replace(source, destination)
            _fsync_dir(source.parent)
            _fsync_dir(destination.parent)
            move["state"] = "moved"
            self._update_pending(pending)
        return len(candidates)

    def commit_transition(self, pending: dict) -> dict:
        """Atomically make the journaled target manifest active."""
        current = self.manifest()
        if current["revision"] != pending["from_revision"]:
            raise WorkspaceStorageError("workspace changed during transition")
        self._atomic_write_json(self.backup_path, current)
        self._atomic_write_json(self.manifest_path, pending["target_manifest"])
        pending["phase"] = "manifest_committed"
        self._update_pending(pending)
        return pending["target_manifest"]

    def complete_transition(self, pending: dict) -> None:
        pending["phase"] = "complete"
        archive = self.archive_dir / pending["operation_id"]
        self._atomic_write_json(archive / "operation.json", pending)
        self.pending_path.unlink(missing_ok=True)
        _fsync_dir(self.state_dir)

    def rollback_uncommitted(
        self, pending: dict, *, allow_manifest_recovery: bool = False
    ) -> dict:
        """Restore moved runtime files when a manifest switch did not happen."""
        current = self.manifest(allow_recovery=allow_manifest_recovery)
        if current["revision"] != pending["from_revision"]:
            raise WorkspaceStorageError("cannot roll back a committed workspace transition")
        pending["phase"] = "rolling_back"
        self._update_pending(pending)
        failures = []
        for item in reversed(pending.get("moved", [])):
            source = Path(item["source"])
            archived = Path(item["archive"])
            if archived.exists() and source.exists():
                failures.append(
                    f"both source and archive exist for {archived} -> {source}"
                )
                continue
            if archived.exists():
                try:
                    source.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(archived, source)
                    _fsync_dir(archived.parent)
                    _fsync_dir(source.parent)
                except OSError as exc:
                    failures.append(f"{archived} -> {source}: {exc}")
                    continue
            elif not source.exists():
                failures.append(f"both source and archive are missing: {source}")
                continue
            item["state"] = "restored"
            self._update_pending(pending)
        if failures:
            pending["phase"] = "rollback_failed"
            pending["errors"] = failures
            self._update_pending(pending)
            raise WorkspaceStorageError("workspace rollback failed: " + "; ".join(failures))
        pending["phase"] = "rolled_back"
        archive = self.archive_dir / pending["operation_id"]
        self._atomic_write_json(archive / "operation.json", pending)
        self.pending_path.unlink(missing_ok=True)
        return pending

    def recover_storage(self) -> dict | None:
        """Deterministically finish storage recovery; graph recovery is external."""
        current = self.manifest(allow_recovery=True)
        manifest_recovery = self.manifest_recovery()
        pending = self.pending()
        if pending is None and manifest_recovery is None:
            return None
        if pending is None:
            return {
                "operation_id": manifest_recovery["operation_id"],
                "operation": "recover-manifest-backup",
                "phase": "pending_graph_reconciliation",
                "manifest_recovery": manifest_recovery,
            }
        if current["revision"] == pending.get("from_revision"):
            recovered = self.rollback_uncommitted(
                pending, allow_manifest_recovery=manifest_recovery is not None
            )
            if manifest_recovery is not None:
                recovered["manifest_recovery"] = manifest_recovery
            return recovered
        target = pending.get("target_manifest", {})
        if current["revision"] == target.get("revision"):
            if manifest_recovery is not None:
                pending["manifest_recovery"] = manifest_recovery
            return pending
        raise WorkspaceStorageError("pending transition does not match the active manifest")
