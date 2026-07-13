import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.db import GRAPH_NAMESPACE
from app.security import RATE_LIMITER
from app.server import (
    _assert_rocketride_readonly_credentials,
    _pipeline_uses_graph,
    _workspace_result,
    app,
)


class ProtectedRouteTests(unittest.TestCase):
    def setUp(self):
        RATE_LIMITER.clear()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    def test_static_config_is_served(self):
        response = self.client.get("/config.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("window.fetch", response.text)
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    def test_mutation_and_agent_routes_default_deny(self):
        calls = [
            ("post", "/api/reset", None),
            ("post", "/api/run/adam2014-m1", {"params": {}}),
            ("post", "/api/upload", {"text": "x" * 200}),
            ("post", "/api/ask", {"question": "What is tested?"}),
        ]
        with patch.dict(os.environ, {}, clear=True):
            for method, path, payload in calls:
                response = getattr(self.client, method)(path, json=payload)
                self.assertEqual(response.status_code, 503, path)

    def test_valid_key_reaches_execution_spine(self):
        key = "k" * 32
        expected = {"run_id": "run-test", "method_id": "adam2014-m1", "error": None}
        with (
            patch.dict(os.environ, {"VERIGRAPH_API_KEY": key}, clear=True),
            patch("app.server._execute_and_curate", return_value=expected) as execute,
        ):
            response = self.client.post(
                "/api/run/adam2014-m1?backend=local",
                headers={"Authorization": f"Bearer {key}"},
                json={"params": {"steps": 5}},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), expected)
        execute.assert_called_once_with("adam2014-m1", "local", {"steps": 5})

    def test_invalid_identifier_is_rejected_before_execution(self):
        key = "k" * 32
        with patch.dict(os.environ, {"VERIGRAPH_API_KEY": key}, clear=True):
            response = self.client.post(
                "/api/run/not-a-method?backend=local",
                headers={"Authorization": f"Bearer {key}"},
                json={"params": {}},
            )
        self.assertEqual(response.status_code, 400)

    def test_partial_workspace_transition_requires_recovery(self):
        with self.assertRaises(HTTPException) as caught:
            _workspace_result(
                {
                    "ok": False,
                    "partial": True,
                    "message": "recovery pending",
                    "sync": {"operation_id": "op-1", "stage": "graph"},
                }
            )
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail["operation_id"], "op-1")

    def test_chunked_registration_body_is_bounded_without_content_length(self):
        def chunks():
            yield b'{"email":"person@example.com","timezone":"'
            yield b"x" * 1_024
            yield b'"}'

        with patch.dict(os.environ, {"VERIGRAPH_MAX_REQUEST_BYTES": "1024"}, clear=True):
            request = self.client.build_request(
                "POST",
                "/api/register",
                content=chunks(),
                headers={"Content-Type": "application/json"},
            )
            self.assertNotIn("content-length", request.headers)
            response = self.client.send(request)

        self.assertEqual(response.status_code, 413, response.text)
        self.assertIn("1024 byte limit", response.json()["detail"])


class _TrackingSlot:
    def __init__(self):
        self.entered = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *_args):
        self.entered = False


class IngestionConcurrencyTests(unittest.TestCase):
    def setUp(self):
        RATE_LIMITER.clear()
        self.client = TestClient(app)
        self.key = "k" * 32

    def tearDown(self):
        self.client.close()

    def test_pdf_parsing_happens_inside_ingestion_slot(self):
        slot = _TrackingSlot()

        def parse_pdf(_blob):
            self.assertTrue(slot.entered)
            return "x" * 200

        with (
            patch.dict(os.environ, {"VERIGRAPH_API_KEY": self.key}, clear=True),
            patch("app.server._ingestion_slots", slot),
            patch("app.server._pdf_to_text", side_effect=parse_pdf),
            patch("app.server._ingest_paper_text", return_value={"paper_id": "paper-1"}),
        ):
            response = self.client.post(
                "/api/upload-file",
                headers={"Authorization": f"Bearer {self.key}"},
                files={"file": ("paper.pdf", b"%PDF test", "application/pdf")},
            )

        self.assertEqual(response.status_code, 200, response.text)

    def test_arxiv_fetch_happens_inside_ingestion_slot(self):
        slot = _TrackingSlot()

        def fetch_arxiv(_url):
            self.assertTrue(slot.entered)
            return "x" * 200

        with (
            patch.dict(os.environ, {"VERIGRAPH_API_KEY": self.key}, clear=True),
            patch("app.server._ingestion_slots", slot),
            patch("app.arxiv.fetch_arxiv_text", side_effect=fetch_arxiv),
            patch("app.server._ingest_paper_text", return_value={"paper_id": "paper-1"}),
        ):
            response = self.client.post(
                "/api/upload-arxiv",
                headers={"Authorization": f"Bearer {self.key}"},
                json={"url": "https://arxiv.org/abs/1706.03762"},
            )

        self.assertEqual(response.status_code, 200, response.text)


class RocketRideReadOnlyTests(unittest.TestCase):
    def test_graph_provider_detection_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            graph_pipe = Path(directory, "graph.pipe")
            graph_pipe.write_text(json.dumps({"components": [{"provider": "db_neo4j"}]}))
            execute_pipe = Path(directory, "execute.pipe")
            execute_pipe.write_text(json.dumps({"components": [{"provider": "tool_http"}]}))
            malformed = Path(directory, "malformed.pipe")
            malformed.write_text("not json")

            self.assertTrue(_pipeline_uses_graph(str(graph_pipe)))
            self.assertFalse(_pipeline_uses_graph(str(execute_pipe)))
            self.assertTrue(_pipeline_uses_graph(str(malformed)))

    def test_readonly_probe_is_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                _assert_rocketride_readonly_credentials()

    def test_readonly_probe_rejects_write_identity_reuse(self):
        env = {
            "VERIGRAPH_ENABLE_ROCKETRIDE_DB": "true",
            "ROCKETRIDE_NEO4J_URI": "neo4j://example.invalid",
            "ROCKETRIDE_NEO4J_READONLY_USER": "neo4j",
            "ROCKETRIDE_NEO4J_READONLY_PASSWORD": "secret",
            "ROCKETRIDE_NEO4J_DATABASE": "neo4j",
            "ROCKETRIDE_GRAPH_NAMESPACE": GRAPH_NAMESPACE,
            "NEO4J_USERNAME": "neo4j",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "must not reuse"):
                _assert_rocketride_readonly_credentials()

    def test_readonly_probe_rejects_an_identity_that_accepts_writes(self):
        class Result:
            def consume(self):
                return None

        class Transaction:
            def run(self, _cypher):
                return Result()

            def rollback(self):
                return None

        class Session:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def run(self, _cypher, **_params):
                class NamespaceResult:
                    @staticmethod
                    def single():
                        return {"count": 0}

                return NamespaceResult()

            def begin_transaction(self):
                return Transaction()

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
            "ROCKETRIDE_NEO4J_DATABASE": "neo4j",
            "ROCKETRIDE_GRAPH_NAMESPACE": GRAPH_NAMESPACE,
            "NEO4J_USERNAME": "writer",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch("neo4j.GraphDatabase.driver", return_value=Driver()),
            self.assertRaisesRegex(RuntimeError, "permits graph mutations"),
        ):
            _assert_rocketride_readonly_credentials()


if __name__ == "__main__":
    unittest.main()
