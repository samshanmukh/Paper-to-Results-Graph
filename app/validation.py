"""Validation boundaries for identifiers, extraction JSON, and run parameters.

The extraction payload is produced by an LLM and run parameters come from an
HTTP request.  Neither is trusted.  Keep validation here so filesystem paths,
environment variables, and graph identifiers all use the same rules.
"""

from __future__ import annotations

import copy
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


class ValidationError(ValueError):
    """Raised when untrusted structured input violates a public contract."""


MAX_IDENTIFIER_LENGTH = 96
MAX_PARAMETERS = 32
MAX_ABS_PARAMETER = 10_000_000
MAX_CLAIMS = 64
MAX_METHODS = 32
MAX_DATASETS = 64
MAX_RELATIONS = 256

_SAFE_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9]*(?:-[a-z0-9]+)*\Z")
_PAPER_ID = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
_PARAMETER_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_NUMBER_TEXT = re.compile(
    r"[+-]?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?\Z"
)
_ARXIV_ID = re.compile(
    r"(?:\d{2}(?:0[1-9]|1[0-2])\.\d{4,5}|"
    r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?:\.[A-Z]{2})?/"
    r"\d{2}(?:0[1-9]|1[0-2])\d{3})"
    r"(?:v[1-9]\d*)?\Z"
)


def require_safe_identifier(value: Any, *, field: str = "identifier") -> str:
    """Return a filesystem-safe graph identifier or raise ``ValidationError``."""
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    if not value or len(value) > MAX_IDENTIFIER_LENGTH:
        raise ValidationError(
            f"{field} must contain 1-{MAX_IDENTIFIER_LENGTH} characters"
        )
    if not _SAFE_IDENTIFIER.fullmatch(value):
        raise ValidationError(
            f"{field} must use lowercase letters, digits, and single hyphens only"
        )
    return value


def require_paper_id(value: Any, *, field: str = "paper_id") -> str:
    value = require_safe_identifier(value, field=field)
    if not _PAPER_ID.fullmatch(value):
        raise ValidationError(f"{field} must start with a lowercase letter")
    return value


def _require_owned_id(
    value: Any,
    *,
    marker: str,
    owner: str | None,
    field: str,
) -> str:
    value = require_safe_identifier(value, field=field)
    match = re.fullmatch(rf"(.+)-{marker}([1-9]\d*)", value)
    if not match:
        raise ValidationError(f"{field} must have the form <paper_id>-{marker}<number>")
    actual_owner = require_paper_id(match.group(1), field=f"{field} owner")
    if owner is not None and actual_owner != require_paper_id(owner):
        raise ValidationError(f"{field} must be owned by paper '{owner}'")
    return value


def require_claim_id(
    value: Any, *, owner: str | None = None, field: str = "claim_id"
) -> str:
    return _require_owned_id(
        value, marker="c", owner=owner, field=field
    )


def require_method_id(
    value: Any, *, owner: str | None = None, field: str = "method_id"
) -> str:
    return _require_owned_id(
        value, marker="m", owner=owner, field=field
    )


def require_parameter_name(value: Any, *, field: str = "parameter name") -> str:
    if not isinstance(value, str) or not _PARAMETER_NAME.fullmatch(value):
        raise ValidationError(
            f"{field} must start with a letter and contain only letters, digits, and underscores"
        )
    return value


def _number(
    value: Any, *, kind: str, field: str, allow_numeric_string: bool = True
) -> int | float:
    if isinstance(value, bool):
        raise ValidationError(f"{field} must be numeric, not boolean")

    parsed: int | float
    if isinstance(value, (int, float)):
        parsed = value
    elif allow_numeric_string and isinstance(value, str) and _NUMBER_TEXT.fullmatch(value):
        try:
            parsed = int(value) if re.fullmatch(r"[+-]?(?:0|[1-9]\d*)", value) else float(value)
        except (OverflowError, ValueError) as exc:
            raise ValidationError(f"{field} is not a finite number") from exc
    else:
        expected = "a JSON number or numeric string" if allow_numeric_string else "a JSON number"
        raise ValidationError(f"{field} must be {expected}")

    if not math.isfinite(parsed):
        raise ValidationError(f"{field} must be finite")
    if abs(parsed) > MAX_ABS_PARAMETER:
        raise ValidationError(
            f"{field} must be between {-MAX_ABS_PARAMETER} and {MAX_ABS_PARAMETER}"
        )
    if kind == "integer":
        if not isinstance(parsed, int):
            raise ValidationError(f"{field} must be an integer")
        return parsed
    if kind != "number":
        raise ValidationError(f"{field} has unsupported type '{kind}'")
    return parsed


_MISSING = object()


def _bound(definition: Mapping[str, Any], long: str, short: str) -> Any:
    if long in definition and short in definition:
        raise ValidationError(f"parameter cannot define both '{long}' and '{short}'")
    if long in definition:
        return definition[long]
    if short in definition:
        return definition[short]
    return _MISSING


def normalize_parameter_definitions(definitions: Any) -> list[dict[str, Any]]:
    """Validate parameter metadata and return a canonical, detached copy.

    ``type`` is optional for existing extraction files and is inferred from the
    default.  Bounds may use ``minimum``/``maximum`` or ``min``/``max``.
    """
    if definitions is None:
        return []
    if isinstance(definitions, str):
        try:
            definitions = json.loads(definitions)
        except json.JSONDecodeError as exc:
            raise ValidationError("parameter definitions are not valid JSON") from exc
    if not isinstance(definitions, list):
        raise ValidationError("parameter definitions must be a list")
    if len(definitions) > MAX_PARAMETERS:
        raise ValidationError(f"at most {MAX_PARAMETERS} parameters are allowed")

    normalized: list[dict[str, Any]] = []
    env_names: set[str] = set()
    allowed = {
        "name", "default", "description", "type",
        "minimum", "maximum", "min", "max",
    }
    for index, raw in enumerate(definitions):
        field = f"params[{index}]"
        if not isinstance(raw, dict):
            raise ValidationError(f"{field} must be an object")
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValidationError(f"{field} has unknown fields: {', '.join(unknown)}")
        if "name" not in raw or "default" not in raw or "description" not in raw:
            raise ValidationError(f"{field} requires name, default, and description")

        name = require_parameter_name(raw["name"], field=f"{field}.name")
        env_name = name.upper()
        if env_name in env_names:
            raise ValidationError(f"duplicate parameter environment name: P2R_{env_name}")
        env_names.add(env_name)

        description = raw["description"]
        if not isinstance(description, str) or not description.strip():
            raise ValidationError(f"{field}.description must be a non-empty string")
        if len(description) > 1000:
            raise ValidationError(f"{field}.description is too long")

        explicit_type = raw.get("type")
        if "type" not in raw:
            explicit_type = (
                "integer"
                if isinstance(raw["default"], int) and not isinstance(raw["default"], bool)
                else "number"
            )
        if explicit_type not in ("integer", "number"):
            raise ValidationError(f"{field}.type must be 'integer' or 'number'")
        default = _number(
            raw["default"],
            kind=explicit_type,
            field=f"{field}.default",
            allow_numeric_string=False,
        )

        minimum_raw = _bound(raw, "minimum", "min")
        maximum_raw = _bound(raw, "maximum", "max")
        minimum = (
            _number(
                minimum_raw,
                kind=explicit_type,
                field=f"{field}.minimum",
                allow_numeric_string=False,
            )
            if minimum_raw is not _MISSING else None
        )
        maximum = (
            _number(
                maximum_raw,
                kind=explicit_type,
                field=f"{field}.maximum",
                allow_numeric_string=False,
            )
            if maximum_raw is not _MISSING else None
        )
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValidationError(f"{field}.minimum cannot exceed maximum")
        if minimum is not None and default < minimum:
            raise ValidationError(f"{field}.default is below minimum")
        if maximum is not None and default > maximum:
            raise ValidationError(f"{field}.default is above maximum")

        item: dict[str, Any] = {
            "name": name,
            "default": default,
            "description": description.strip(),
            "type": explicit_type,
        }
        if minimum is not None:
            item["minimum"] = minimum
        if maximum is not None:
            item["maximum"] = maximum
        normalized.append(item)
    return normalized


def normalize_parameter_overrides(
    params: Any,
    definitions: Sequence[Mapping[str, Any]] | str | None = None,
) -> dict[str, int | float]:
    """Normalize HTTP parameter overrides and enforce optional method metadata."""
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise ValidationError("params must be an object")
    if len(params) > MAX_PARAMETERS:
        raise ValidationError(f"at most {MAX_PARAMETERS} parameter overrides are allowed")

    enforce_definitions = definitions is not None
    normalized_definitions = (
        normalize_parameter_definitions(definitions) if definitions is not None else []
    )
    by_env = {item["name"].upper(): item for item in normalized_definitions}
    seen: set[str] = set()
    result: dict[str, int | float] = {}
    for raw_name, raw_value in params.items():
        name = require_parameter_name(raw_name)
        env_name = name.upper()
        if env_name in seen:
            raise ValidationError(f"duplicate parameter environment name: P2R_{env_name}")
        seen.add(env_name)

        definition = by_env.get(env_name)
        if enforce_definitions and definition is None:
            raise ValidationError(f"unknown parameter '{name}'")
        canonical_name = definition["name"] if definition else name
        kind = definition["type"] if definition else (
            "integer"
            if isinstance(raw_value, int) and not isinstance(raw_value, bool)
            else "number"
        )
        value = _number(raw_value, kind=kind, field=f"parameter '{canonical_name}'")
        if definition:
            minimum = definition.get("minimum")
            maximum = definition.get("maximum")
            if minimum is not None and value < minimum:
                raise ValidationError(f"parameter '{canonical_name}' is below minimum {minimum}")
            if maximum is not None and value > maximum:
                raise ValidationError(f"parameter '{canonical_name}' exceeds maximum {maximum}")
        result[canonical_name] = value
    return result


def parameter_env(
    params: Any,
    definitions: Sequence[Mapping[str, Any]] | str | None = None,
) -> dict[str, str]:
    """Build the constrained environment mapping used by experiment runners."""
    normalized = normalize_parameter_overrides(params, definitions)
    return {f"P2R_{name.upper()}": str(value) for name, value in normalized.items()}


def _text(
    value: Any, *, field: str, maximum: int, nullable: bool = False
) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be a non-empty string")
    if len(value) > maximum:
        raise ValidationError(f"{field} exceeds {maximum} characters")
    return value


def _object_keys(
    value: Mapping[str, Any], *, field: str, required: set[str], allowed: set[str]
) -> list[str]:
    errors: list[str] = []
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        errors.append(f"{field} missing fields: {', '.join(missing)}")
    if unknown:
        errors.append(f"{field} has unknown fields: {', '.join(unknown)}")
    return errors


def validate_extraction(data: Any) -> list[str]:
    """Return all practical schema violations for an untrusted extraction."""
    if not isinstance(data, dict):
        return ["extraction must be an object"]

    errors: list[str] = []
    allowed_top = {
        "paper", "claims", "methods", "datasets", "cites", "claim_relations"
    }
    errors.extend(_object_keys(
        data,
        field="extraction",
        required=allowed_top,
        allowed=allowed_top,
    ))

    paper = data.get("paper")
    paper_id: str | None = None
    if not isinstance(paper, dict):
        errors.append("paper must be an object")
    else:
        paper_required = {"id", "title", "authors", "year", "topic"}
        paper_allowed = paper_required | {"arxiv"}
        errors.extend(_object_keys(
            paper, field="paper", required=paper_required, allowed=paper_allowed
        ))
        try:
            paper_id = require_paper_id(paper.get("id"), field="paper.id")
        except ValidationError as exc:
            errors.append(str(exc))
        for key, maximum in (("title", 1000), ("topic", MAX_IDENTIFIER_LENGTH)):
            try:
                value = _text(paper.get(key), field=f"paper.{key}", maximum=maximum)
                if key == "topic" and value is not None:
                    require_safe_identifier(value, field="paper.topic")
            except ValidationError as exc:
                errors.append(str(exc))
        authors = paper.get("authors")
        if not isinstance(authors, list) or not authors:
            errors.append("paper.authors must be a non-empty list")
        elif len(authors) > 100:
            errors.append("paper.authors may contain at most 100 authors")
        else:
            seen_authors: set[str] = set()
            for index, author in enumerate(authors):
                try:
                    author = _text(
                        author, field=f"paper.authors[{index}]", maximum=300
                    )
                    key = author.casefold() if author else ""
                    if key in seen_authors:
                        errors.append(f"duplicate paper author: {author}")
                    seen_authors.add(key)
                except ValidationError as exc:
                    errors.append(str(exc))
        year = paper.get("year")
        max_year = datetime.now(timezone.utc).year + 2
        if isinstance(year, bool) or not isinstance(year, int):
            errors.append("paper.year must be an integer")
        elif not 1600 <= year <= max_year:
            errors.append(f"paper.year must be between 1600 and {max_year}")
        arxiv = paper.get("arxiv")
        if arxiv is not None and (
            not isinstance(arxiv, str) or not _ARXIV_ID.fullmatch(arxiv)
        ):
            errors.append("paper.arxiv must be a canonical arXiv id or null")

    claims = data.get("claims")
    claim_ids: set[str] = set()
    if not isinstance(claims, list):
        errors.append("claims must be a list")
    elif not claims:
        errors.append("at least one claim is required")
    elif len(claims) > MAX_CLAIMS:
        errors.append(f"at most {MAX_CLAIMS} claims are allowed")
    else:
        for index, claim in enumerate(claims):
            field = f"claims[{index}]"
            if not isinstance(claim, dict):
                errors.append(f"{field} must be an object")
                continue
            errors.extend(_object_keys(
                claim,
                field=field,
                required={"id", "text"},
                allowed={"id", "text", "metric"},
            ))
            try:
                claim_id = require_claim_id(
                    claim.get("id"), owner=paper_id, field=f"{field}.id"
                )
                if claim_id in claim_ids:
                    errors.append(f"duplicate claim id: {claim_id}")
                claim_ids.add(claim_id)
            except ValidationError as exc:
                errors.append(str(exc))
            for key, nullable in (("text", False), ("metric", True)):
                try:
                    _text(
                        claim.get(key), field=f"{field}.{key}", maximum=5000,
                        nullable=nullable,
                    )
                except ValidationError as exc:
                    errors.append(str(exc))

    methods = data.get("methods")
    method_ids: set[str] = set()
    if not isinstance(methods, list):
        errors.append("methods must be a list")
    elif not methods:
        errors.append("at least one method is required")
    elif len(methods) > MAX_METHODS:
        errors.append(f"at most {MAX_METHODS} methods are allowed")
    else:
        required = {"id", "name", "description", "runnable_hint"}
        for index, method in enumerate(methods):
            field = f"methods[{index}]"
            if not isinstance(method, dict):
                errors.append(f"{field} must be an object")
                continue
            errors.extend(_object_keys(
                method,
                field=field,
                required=required,
                allowed=required | {"params"},
            ))
            try:
                method_id = require_method_id(
                    method.get("id"), owner=paper_id, field=f"{field}.id"
                )
                if method_id in method_ids:
                    errors.append(f"duplicate method id: {method_id}")
                method_ids.add(method_id)
            except ValidationError as exc:
                errors.append(str(exc))
            for key, maximum in (
                ("name", 500), ("description", 10_000), ("runnable_hint", 20_000)
            ):
                try:
                    _text(method.get(key), field=f"{field}.{key}", maximum=maximum)
                except ValidationError as exc:
                    errors.append(str(exc))
            try:
                params = method.get("params", [])
                if "params" in method and params is None:
                    raise ValidationError("parameter definitions must be a list")
                normalize_parameter_definitions(params)
            except ValidationError as exc:
                errors.append(f"{field}.{exc}")

    datasets = data.get("datasets")
    dataset_ids: set[str] = set()
    if not isinstance(datasets, list):
        errors.append("datasets must be a list")
    elif len(datasets) > MAX_DATASETS:
        errors.append(f"at most {MAX_DATASETS} datasets are allowed")
    else:
        for index, dataset in enumerate(datasets):
            field = f"datasets[{index}]"
            if not isinstance(dataset, dict):
                errors.append(f"{field} must be an object")
                continue
            errors.extend(_object_keys(
                dataset,
                field=field,
                required={"id", "name"},
                allowed={"id", "name"},
            ))
            try:
                dataset_id = require_safe_identifier(
                    dataset.get("id"), field=f"{field}.id"
                )
                if dataset_id in dataset_ids:
                    errors.append(f"duplicate dataset id: {dataset_id}")
                dataset_ids.add(dataset_id)
            except ValidationError as exc:
                errors.append(str(exc))
            try:
                _text(dataset.get("name"), field=f"{field}.name", maximum=500)
            except ValidationError as exc:
                errors.append(str(exc))

    cites = data.get("cites")
    cited_ids: set[str] = set()
    if not isinstance(cites, list):
        errors.append("cites must be a list")
    elif len(cites) > 256:
        errors.append("at most 256 citations are allowed")
    else:
        for index, cited in enumerate(cites):
            try:
                cited_id = require_paper_id(cited, field=f"cites[{index}]")
                if cited_id == paper_id:
                    errors.append("paper cannot cite itself")
                if cited_id in cited_ids:
                    errors.append(f"duplicate citation: {cited_id}")
                cited_ids.add(cited_id)
            except ValidationError as exc:
                errors.append(str(exc))

    relations = data.get("claim_relations")
    relation_pairs: set[tuple[str, str]] = set()
    if not isinstance(relations, list):
        errors.append("claim_relations must be a list")
    elif len(relations) > MAX_RELATIONS:
        errors.append(f"at most {MAX_RELATIONS} claim relations are allowed")
    else:
        for index, relation in enumerate(relations):
            field = f"claim_relations[{index}]"
            if not isinstance(relation, dict):
                errors.append(f"{field} must be an object")
                continue
            errors.extend(_object_keys(
                relation,
                field=field,
                required={"from", "to", "type"},
                allowed={"from", "to", "type"},
            ))
            source: str | None = None
            target: str | None = None
            try:
                source = require_claim_id(
                    relation.get("from"), owner=paper_id, field=f"{field}.from"
                )
                if source not in claim_ids:
                    errors.append(f"{field}.from is not a claim in this extraction")
            except ValidationError as exc:
                errors.append(str(exc))
            try:
                target = require_claim_id(relation.get("to"), field=f"{field}.to")
            except ValidationError as exc:
                errors.append(str(exc))
            relation_type = relation.get("type")
            if relation_type not in {"SUPPORTS", "CONTRADICTS"}:
                errors.append(f"{field}.type must be SUPPORTS or CONTRADICTS")
            if source and target:
                if source == target:
                    errors.append(f"{field} cannot relate a claim to itself")
                pair = (source, target)
                if pair in relation_pairs:
                    errors.append(f"duplicate claim relation: {source} -> {target}")
                relation_pairs.add(pair)
    return errors


def normalize_extraction(data: Any) -> dict[str, Any]:
    """Validate an extraction and return a deep copy safe for persistence."""
    errors = validate_extraction(data)
    if errors:
        raise ValidationError("extraction failed validation: " + "; ".join(errors))
    normalized = copy.deepcopy(data)
    for method in normalized["methods"]:
        if "params" in method:
            method["params"] = normalize_parameter_definitions(method["params"])
    return normalized


require_valid_extraction = normalize_extraction
