#!/usr/bin/env python3
"""Unit tests for Cognee memory helpers (mocked, no live API)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class CogneeConfigTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {"COGNEE_ENABLED": "false", "COGNEE_CLOUD": "false"}, clear=False):
            from app import cognee_memory

            self.assertFalse(cognee_memory.is_enabled())

    def test_enabled_with_flag_and_gateway(self):
        with patch.dict(
            os.environ,
            {
                "COGNEE_ENABLED": "true",
                "ROCKETRIDE_GATEWAY_BASE_URL": "https://example.test/v1/app_x",
                "ROCKETRIDE_GATEWAY_KEY": "key",
                "ROCKETRIDE_GATEWAY_MODEL": "x-ai/grok-4.3",
            },
            clear=False,
        ):
            from app import cognee_memory

            self.assertTrue(cognee_memory.is_enabled())
            self.assertFalse(cognee_memory.is_cloud_mode())
            self.assertEqual(cognee_memory.dataset_name(), "verigraph")
            self.assertEqual(cognee_memory._llm_model(), "openai/x-ai/grok-4.3")

    def test_enabled_cloud_mode(self):
        with patch.dict(
            os.environ,
            {
                "COGNEE_ENABLED": "true",
                "COGNEE_CLOUD": "true",
                "COGNEE_SERVICE_URL": "https://tenant.aws.cognee.ai",
                "COGNEE_API_KEY": "ck_test",
            },
            clear=False,
        ):
            from app import cognee_memory

            self.assertTrue(cognee_memory.is_enabled())
            self.assertTrue(cognee_memory.is_cloud_mode())
            self.assertEqual(cognee_memory.dataset_name(), "default_dataset")


class CogneeDocumentTests(unittest.TestCase):
    def test_format_run_document_includes_verdicts(self):
        from app.cognee_memory import _format_run_document

        doc = _format_run_document(
            {
                "run_id": "run-wilson2017-m1-test",
                "method_id": "wilson2017-m1",
                "backend": "daytona",
                "implementation_source": "llm",
                "exit_code": 0,
                "duration_s": 4.2,
                "stdout": "train error GD=0.000 Adam=0.425",
                "result": {
                    "metrics": {"test_error_gd": 0.0, "test_error_adam": 0.425},
                    "claim_checks": [
                        {
                            "claim_id": "wilson2017-c1",
                            "verdict": "VALIDATES",
                            "detail": "Adam generalized worse",
                        }
                    ],
                },
            }
        )
        self.assertIn("wilson2017-c1", doc)
        self.assertIn("VALIDATES", doc)
        self.assertIn("0.425", doc)
        self.assertIn("implementation_source=llm", doc)
        self.assertIn("provisional=true", doc)


class CogneeRecallTests(unittest.IsolatedAsyncioTestCase):
    async def test_recall_returns_text_snippets(self):
        mock_item = MagicMock(source="graph", text="GD 0.000 vs Adam 0.425")
        mock_cognee = MagicMock()
        mock_cognee.run_migrations = AsyncMock()
        mock_cognee.recall = AsyncMock(return_value=[mock_item])

        with patch.dict(
            os.environ,
            {
                "COGNEE_ENABLED": "true",
                "ROCKETRIDE_GATEWAY_BASE_URL": "https://example.test/v1/app_x",
                "ROCKETRIDE_GATEWAY_KEY": "key",
            },
            clear=False,
        ):
            with patch("app.cognee_memory._import_cognee", return_value=mock_cognee):
                with patch("app.cognee_memory._ensure_connected", new=AsyncMock()):
                    from app.cognee_memory import recall

                    out = await recall("Adam generalization")
        self.assertEqual(out, ["GD 0.000 vs Adam 0.425"])


if __name__ == "__main__":
    unittest.main()
