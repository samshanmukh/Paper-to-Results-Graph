#!/usr/bin/env python3
"""Validate RocketRide pipeline schema fields and Verigraph security invariants."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = ROOT / "pipelines"
HTTP_SCHEMA_PATH = ROOT / ".rocketride" / "schema" / "tool_http_request.json"
RUN_URL_PATTERN = (
    r"^http://127\.0\.0\.1:8787/api/run/[a-z0-9]+(?:-[a-z0-9]+)*$"
)
GRAPH_NAMESPACE_PLACEHOLDER = "${ROCKETRIDE_GRAPH_NAMESPACE}"
GRAPH_DATABASE_PLACEHOLDER = "${ROCKETRIDE_NEO4J_DATABASE}"


class PipelineValidationError(ValueError):
    """One or more local pipeline contracts are invalid."""


def _is_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def _validate_schema(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    """Validate the JSON Schema subset used by tool_http_request."""
    errors: list[str] = []
    expected = schema.get("type")
    if expected and not _is_type(value, expected):
        return [f"{path}: expected {expected}, got {type(value).__name__}"]

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value {value!r} is not in {schema['enum']!r}")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required field {key!r}")
        for key, child in schema.get("properties", {}).items():
            if key in value:
                errors.extend(_validate_schema(value[key], child, f"{path}.{key}"))

    if isinstance(value, list):
        minimum = schema.get("minItems")
        if minimum is not None and len(value) < minimum:
            errors.append(f"{path}: expected at least {minimum} items")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                errors.extend(_validate_schema(item, item_schema, f"{path}[{index}]"))

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: must be <= {schema['maximum']}")
    return errors


def _validate_http_component(
    pipeline_path: Path,
    pipeline: dict[str, Any],
    component: dict[str, Any],
    schema: dict[str, Any],
) -> list[str]:
    prefix = f"{pipeline_path.name}:{component.get('id', '<unknown>')}"
    config = component.get("config")
    errors = _validate_schema(config, schema, f"{prefix}.config")
    if not isinstance(config, dict):
        return errors

    expected_methods = {
        "allowGET": False,
        "allowPOST": True,
        "allowPUT": False,
        "allowPATCH": False,
        "allowDELETE": False,
        "allowHEAD": False,
        "allowOPTIONS": False,
    }
    for field, expected in expected_methods.items():
        if config.get(field) is not expected:
            errors.append(f"{prefix}.config.{field}: expected {expected}")

    whitelist = config.get("urlWhitelist")
    expected_whitelist = [{"whitelistPattern": RUN_URL_PATTERN}]
    if whitelist != expected_whitelist:
        errors.append(
            f"{prefix}.config.urlWhitelist: must contain only the exact run URL pattern"
        )
    else:
        try:
            re.compile(whitelist[0]["whitelistPattern"])
        except re.error as exc:
            errors.append(f"{prefix}.config.urlWhitelist: invalid regex: {exc}")

    instructions = "\n".join(
        instruction
        for node in pipeline.get("components", [])
        if isinstance(node, dict)
        for instruction in (node.get("config") or {}).get("instructions", [])
        if isinstance(instruction, str)
    )
    if "PIPELINE_API_KEY" in instructions or "X-Verigraph-Pipeline-Key" in instructions:
        errors.append(f"{prefix}: executor instructions must not receive credentials")
    return errors


def _validate_graph_component(
    pipeline_path: Path, component: dict[str, Any]
) -> list[str]:
    prefix = f"{pipeline_path.name}:{component.get('id', '<unknown>')}"
    config = component.get("config") or {}
    default = config.get("default") or {}
    description = default.get("db_description", "")
    errors: list[str] = []
    if default.get("database") != GRAPH_DATABASE_PLACEHOLDER:
        errors.append(f"{prefix}: database must use {GRAPH_DATABASE_PLACEHOLDER}")
    if default.get("allow_execute") is not False:
        errors.append(f"{prefix}: allow_execute must be false")
    if ":Verigraph" not in description:
        errors.append(f"{prefix}: graph description does not require :Verigraph")
    if GRAPH_NAMESPACE_PLACEHOLDER not in description:
        errors.append(
            f"{prefix}: graph description does not require the configured namespace"
        )
    if "EVERY node pattern" not in description:
        errors.append(f"{prefix}: graph description does not scope every node pattern")
    return errors


def validate_pipelines() -> list[Path]:
    http_document = json.loads(HTTP_SCHEMA_PATH.read_text(encoding="utf-8"))
    http_schema = http_document["Pipe"]["schema"]
    errors: list[str] = []
    checked: list[Path] = []

    for pipeline_path in sorted(PIPELINE_DIR.glob("*.pipe")):
        try:
            pipeline = json.loads(pipeline_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{pipeline_path.name}: invalid JSON: {exc}")
            continue
        checked.append(pipeline_path)
        components = pipeline.get("components")
        if not isinstance(components, list):
            errors.append(f"{pipeline_path.name}: components must be an array")
            continue
        for component in components:
            if not isinstance(component, dict):
                errors.append(f"{pipeline_path.name}: component must be an object")
                continue
            if component.get("provider") == "tool_http_request":
                errors.extend(
                    _validate_http_component(
                        pipeline_path, pipeline, component, http_schema
                    )
                )
            if component.get("provider") == "db_neo4j":
                errors.extend(_validate_graph_component(pipeline_path, component))

    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for variable in (
        "VERIGRAPH_ENABLE_ROCKETRIDE_EXECUTOR",
        "VERIGRAPH_GRAPH_NAMESPACE",
        "ROCKETRIDE_GRAPH_NAMESPACE",
        "ROCKETRIDE_NEO4J_DATABASE",
    ):
        if not re.search(rf"(?m)^{re.escape(variable)}=", env_example):
            errors.append(f".env.example: missing {variable}")

    if errors:
        raise PipelineValidationError("\n".join(errors))
    return checked


def main() -> int:
    try:
        checked = validate_pipelines()
    except PipelineValidationError as exc:
        print(f"RocketRide pipeline validation failed:\n{exc}")
        return 1
    print(f"Validated {len(checked)} RocketRide pipeline files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
