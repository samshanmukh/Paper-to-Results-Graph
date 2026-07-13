import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.db import GRAPH_NAMESPACE
from app.security import require_api_key
from app.server import _assert_rocketride_readonly_credentials
from scripts.validate_pipelines import validate_pipelines


MAIN_KEY = "main-api-key-which-is-at-least-32-characters"


def _protected_client() -> TestClient:
    api = FastAPI()

    @api.api_route("/api/run/{method_id}", methods=["GET", "POST"])
    def run_endpoint(method_id: str, request: Request):
        require_api_key(request)
        return {"method_id": method_id}

    @api.post("/api/reset")
    def reset_endpoint(request: Request):
        require_api_key(request)
        return {"ok": True}

    return TestClient(
        api,
        base_url="http://127.0.0.1:8787",
        client=("127.0.0.1", 50_000),
    )


def test_repository_pipelines_validate_against_local_contracts():
    checked = validate_pipelines()
    assert {path.name for path in checked} >= {
        "paper-execute.pipe",
        "paper-orchestrator.pipe",
        "verigraph.pipe",
    }


def test_executor_opt_in_authorizes_exact_non_browser_loopback_run_post_only():
    client = _protected_client()
    env = {
        "VERIGRAPH_API_KEY": MAIN_KEY,
        "VERIGRAPH_ENABLE_ROCKETRIDE_EXECUTOR": "true",
    }
    with patch.dict(os.environ, env, clear=True):
        allowed = client.post("/api/run/adam2014-m1")
        reset = client.post("/api/reset")
        read = client.get("/api/run/adam2014-m1")
        unsafe_id = client.post("/api/run/Adam2014-m1")
        browser = client.post(
            "/api/run/adam2014-m1", headers={"Sec-Fetch-Site": "same-origin"}
        )
        referred = client.post(
            "/api/run/adam2014-m1", headers={"Referer": "http://127.0.0.1:8787/demo"}
        )
        forwarded = client.post(
            "/api/run/adam2014-m1", headers={"X-Forwarded-For": "127.0.0.1"}
        )

    assert allowed.status_code == 200
    assert reset.status_code == 401
    assert read.status_code == 401
    assert unsafe_id.status_code == 401
    assert browser.status_code == 401
    assert referred.status_code == 401
    assert forwarded.status_code == 401


def test_executor_bypass_is_disabled_by_default():
    client = _protected_client()
    env = {"VERIGRAPH_API_KEY": MAIN_KEY}
    with patch.dict(os.environ, env, clear=True):
        response = client.post("/api/run/adam2014-m1")
    assert response.status_code == 401


def test_readonly_probe_uses_the_pipeline_database_exactly():
    databases: list[str] = []

    class Forbidden(Exception):
        code = "Neo.ClientError.Security.Forbidden"

    class Transaction:
        def run(self, _cypher):
            raise Forbidden()

        def rollback(self):
            return None

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def run(self, _cypher, **_params):
            class Result:
                @staticmethod
                def single():
                    return {"count": 0}

            return Result()

        def begin_transaction(self):
            return Transaction()

    class Driver:
        def verify_connectivity(self):
            return None

        def session(self, *, database):
            databases.append(database)
            return Session()

        def close(self):
            return None

    env = {
        "VERIGRAPH_ENABLE_ROCKETRIDE_DB": "true",
        "ROCKETRIDE_NEO4J_URI": "neo4j://example.invalid",
        "ROCKETRIDE_NEO4J_READONLY_USER": "reader",
        "ROCKETRIDE_NEO4J_READONLY_PASSWORD": "secret",
        "ROCKETRIDE_NEO4J_DATABASE": "research-evidence",
        "ROCKETRIDE_GRAPH_NAMESPACE": GRAPH_NAMESPACE,
        "NEO4J_USERNAME": "writer",
        "NEO4J_DATABASE": "wrong-database",
    }
    with (
        patch.dict(os.environ, env, clear=True),
        patch("neo4j.GraphDatabase.driver", return_value=Driver()),
        patch("neo4j.exceptions.ClientError", Forbidden),
    ):
        _assert_rocketride_readonly_credentials()

    assert databases == ["research-evidence"] * 5


def test_readonly_probe_rejects_foreign_verigraph_namespaces():
    class Result:
        @staticmethod
        def single():
            return {"count": 1}

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def run(self, _cypher, **_params):
            return Result()

    class Driver:
        def verify_connectivity(self):
            return None

        def session(self, **_kwargs):
            return Session()

        def close(self):
            return None

    env = {
        "VERIGRAPH_ENABLE_ROCKETRIDE_DB": "true",
        "ROCKETRIDE_NEO4J_URI": "neo4j://example.invalid",
        "ROCKETRIDE_NEO4J_READONLY_USER": "reader",
        "ROCKETRIDE_NEO4J_READONLY_PASSWORD": "secret",
        "ROCKETRIDE_NEO4J_DATABASE": "research-evidence",
        "ROCKETRIDE_GRAPH_NAMESPACE": GRAPH_NAMESPACE,
        "NEO4J_USERNAME": "writer",
    }
    with (
        patch.dict(os.environ, env, clear=True),
        patch("neo4j.GraphDatabase.driver", return_value=Driver()),
        pytest.raises(RuntimeError, match="outside the configured namespace"),
    ):
        _assert_rocketride_readonly_credentials()


def test_readonly_probe_requires_an_explicit_pipeline_database():
    env = {
        "VERIGRAPH_ENABLE_ROCKETRIDE_DB": "true",
        "ROCKETRIDE_NEO4J_URI": "neo4j://example.invalid",
        "ROCKETRIDE_NEO4J_READONLY_USER": "reader",
        "ROCKETRIDE_NEO4J_READONLY_PASSWORD": "secret",
        "NEO4J_USERNAME": "writer",
    }
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(RuntimeError, match="not configured"):
            _assert_rocketride_readonly_credentials()
