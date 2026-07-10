#!/usr/bin/env python3
"""Index existing Verigraph papers and runs into Cognee semantic memory."""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.cognee_memory import (
    _format_paper_document,
    _format_run_document,
    is_enabled,
    remember_many_sync,
)


def collect_papers() -> list[tuple[str, list[str]]]:
    papers_dir = os.path.join(ROOT, "papers")
    extracted_dir = os.path.join(papers_dir, "extracted")
    docs: list[tuple[str, list[str]]] = []
    for fname in sorted(os.listdir(extracted_dir)):
        if not fname.endswith(".json"):
            continue
        pid = fname[:-5]
        text_path = os.path.join(papers_dir, f"{pid}.txt")
        if not os.path.isfile(text_path):
            continue
        with open(os.path.join(extracted_dir, fname)) as f:
            data = json.load(f)
        with open(text_path) as f:
            text = f.read()
        doc = _format_paper_document(pid, data["paper"]["title"], text, data)
        docs.append((doc, [pid, "paper"]))
    return docs


def _butterbase_run_record(row: dict) -> dict:
    checks = row.get("claim_checks") or []
    if isinstance(checks, dict):
        checks = checks.get("items") or []
    return {
        "run_id": row.get("id"),
        "method_id": row.get("method_id"),
        "backend": row.get("backend"),
        "exit_code": row.get("exit_code"),
        "duration_s": row.get("duration_s"),
        "stdout": row.get("stdout") or "",
        "error": row.get("error"),
        "result": {
            "metrics": row.get("metrics") or {},
            "claim_checks": checks,
        },
    }


def collect_runs(*, local_only: bool = False) -> list[tuple[str, list[str]]]:
    runs_dir = os.path.join(ROOT, "runs")
    records: dict[str, dict] = {}
    if os.path.isdir(runs_dir):
        for fname in sorted(os.listdir(runs_dir)):
            if not fname.endswith(".json"):
                continue
            with open(os.path.join(runs_dir, fname)) as f:
                record = json.load(f)
            records[record["run_id"]] = record

    if not local_only:
        try:
            from app.butterbase import _req

            remote = _req("GET", "/runs?order=id&limit=1000")
            rows = remote if isinstance(remote, list) else remote.get("data", remote.get("rows", []))
            for row in rows:
                record = _butterbase_run_record(row)
                if record["run_id"]:
                    records[record["run_id"]] = record
        except Exception as exc:
            print(f"warning: could not read Butterbase runs: {exc}", file=sys.stderr)

    docs: list[tuple[str, list[str]]] = []
    for record in records.values():
        method_id = record.get("method_id") or "unknown-method"
        paper_id = method_id.rsplit("-", 1)[0] if "-" in method_id else method_id
        doc = _format_run_document(record)
        docs.append((doc, [record.get("run_id", "run"), method_id, paper_id, "run"]))
    return docs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--papers-only", action="store_true")
    ap.add_argument("--runs-only", action="store_true")
    ap.add_argument("--local-only", action="store_true", help="do not include Butterbase run history")
    args = ap.parse_args()
    if not is_enabled():
        print(
            "Cognee disabled — set COGNEE_ENABLED=true and either "
            "ROCKETRIDE_GATEWAY_* (local) or COGNEE_CLOUD + COGNEE_SERVICE_URL + COGNEE_API_KEY",
            file=sys.stderr,
        )
        return 1

    docs: list[tuple[str, list[str]]] = []
    if not args.runs_only:
        docs.extend(collect_papers())
    if not args.papers_only:
        docs.extend(collect_runs(local_only=args.local_only))
    if not docs:
        print("nothing to sync")
        return 0
    remember_many_sync(docs)
    print(f"synced {len(docs)} documents into Cognee dataset")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
