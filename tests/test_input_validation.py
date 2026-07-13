from __future__ import annotations

import copy
import glob
import json

import pytest

from app.validation import (
    ValidationError,
    normalize_extraction,
    normalize_parameter_definitions,
    normalize_parameter_overrides,
    parameter_env,
    require_claim_id,
    require_method_id,
    require_paper_id,
    require_safe_identifier,
    validate_extraction,
)


def _seed() -> dict:
    with open("papers/extracted/wilson2017.json") as handle:
        return json.load(handle)


def test_all_tracked_extractions_remain_valid():
    paths = sorted(glob.glob("papers/extracted/*.json"))
    assert paths
    for path in paths:
        with open(path) as handle:
            assert validate_extraction(json.load(handle)) == [], path


@pytest.mark.parametrize(
    "value",
    ["", "../paper", "paper/child", "paper.id", "Paper", "paper--id", 12, None],
)
def test_unsafe_identifiers_are_rejected(value):
    with pytest.raises(ValidationError):
        require_safe_identifier(value)


def test_owned_entity_identifiers_are_enforced():
    assert require_paper_id("wilson2017") == "wilson2017"
    assert require_claim_id("wilson2017-c1", owner="wilson2017") == "wilson2017-c1"
    assert require_method_id("wilson2017-m12", owner="wilson2017") == "wilson2017-m12"
    with pytest.raises(ValidationError, match="owned by"):
        require_claim_id("adam2014-c1", owner="wilson2017")
    with pytest.raises(ValidationError, match="form"):
        require_method_id("wilson2017-c1", owner="wilson2017")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda d: d.update(claims={}), "claims must be a list"),
        (lambda d: d["paper"].update(year="2017"), "paper.year must be an integer"),
        (lambda d: d["paper"].update(id="../wilson"), "paper.id"),
        (lambda d: d["claims"][0].update(id="adam2014-c1"), "owned by"),
        (lambda d: d["methods"][0].update(id="adam2014-m1"), "owned by"),
        (lambda d: d["methods"][0].update(params={}), "parameter definitions must be a list"),
        (lambda d: d["methods"][0].update(params=None), "parameter definitions must be a list"),
        (lambda d: d["claim_relations"][0].update(type="LIKES"), "SUPPORTS or CONTRADICTS"),
        (lambda d: d["claim_relations"][0].update(to="../../claim"), "claim_relations[0].to"),
    ],
)
def test_malformed_extractions_are_rejected_without_type_errors(mutation, message):
    data = _seed()
    mutation(data)
    assert message in " | ".join(validate_extraction(data))


def test_duplicate_ids_params_citations_and_relations_are_rejected():
    data = _seed()
    data["claims"].append(copy.deepcopy(data["claims"][0]))
    data["datasets"].append(copy.deepcopy(data["datasets"][0]))
    data["cites"].append(data["cites"][0])
    data["claim_relations"].append(copy.deepcopy(data["claim_relations"][0]))
    data["methods"][0]["params"].append({
        "name": "N_TRAIN",
        "default": 5,
        "description": "case-collides in the runner environment",
    })
    errors = " | ".join(validate_extraction(data))
    assert "duplicate claim id" in errors
    assert "duplicate dataset id" in errors
    assert "duplicate citation" in errors
    assert "duplicate claim relation" in errors
    assert "duplicate parameter environment name" in errors


def test_extraction_unknown_fields_and_self_relations_are_rejected():
    data = _seed()
    data["surprise"] = "ignored by the old validator"
    data["claim_relations"][0]["to"] = data["claim_relations"][0]["from"]
    errors = " | ".join(validate_extraction(data))
    assert "unknown fields: surprise" in errors
    assert "cannot relate a claim to itself" in errors


def test_parameter_definitions_infer_types_and_enforce_bounds():
    definitions = normalize_parameter_definitions([
        {
            "name": "steps",
            "default": 20,
            "description": "training steps",
            "minimum": 1,
            "maximum": 100,
        },
        {
            "name": "lr",
            "default": 0.1,
            "description": "learning rate",
            "min": 0.0,
            "max": 1.0,
        },
    ])
    assert definitions[0]["type"] == "integer"
    assert definitions[1]["type"] == "number"
    assert normalize_parameter_overrides(
        {"STEPS": "50", "lr": "0.25"}, definitions
    ) == {"steps": 50, "lr": 0.25}

    with pytest.raises(ValidationError, match="exceeds maximum"):
        normalize_parameter_overrides({"steps": 101}, definitions)
    with pytest.raises(ValidationError, match="unknown parameter"):
        normalize_parameter_overrides({"epochs": 2}, definitions)
    with pytest.raises(ValidationError, match="unknown parameter"):
        normalize_parameter_overrides({"epochs": 2}, [])
    with pytest.raises(ValidationError, match="integer"):
        normalize_parameter_overrides({"steps": "2.5"}, definitions)


@pytest.mark.parametrize(
    "definition",
    [
        {"name": "steps", "default": "20", "description": "steps"},
        {"name": "steps", "default": 20, "description": "steps", "type": None},
        {"name": "steps", "default": 20, "description": "steps", "minimum": None},
    ],
)
def test_parameter_definition_numbers_and_types_must_be_json_primitives(definition):
    with pytest.raises(ValidationError):
        normalize_parameter_definitions([definition])


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "1e999", [], {}])
def test_unsafe_parameter_values_are_rejected(value):
    with pytest.raises(ValidationError):
        normalize_parameter_overrides({"steps": value})


def test_parameter_names_cannot_collide_in_environment():
    with pytest.raises(ValidationError, match="duplicate parameter environment"):
        normalize_parameter_overrides({"steps": 1, "STEPS": 2})
    with pytest.raises(ValidationError):
        normalize_parameter_overrides({"BAD-NAME": 1})


def test_parameter_env_is_bounded_and_canonical():
    definitions = [{
        "name": "T_0",
        "default": 10,
        "description": "restart period",
        "minimum": 1,
        "maximum": 100,
    }]
    assert parameter_env({"t_0": "25"}, definitions) == {"P2R_T_0": "25"}


def test_normalize_extraction_returns_detached_canonical_copy():
    data = _seed()
    normalized = normalize_extraction(data)
    assert normalized is not data
    assert normalized["methods"][0]["params"][0]["type"] == "integer"
    assert "type" not in data["methods"][0]["params"][0]
