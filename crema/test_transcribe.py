"""Integration tests for crema.api.transcribe — zero live network calls.

Mock boundary: unittest.mock.patch("crema.client._transcribe") (transcribe()'s sibling
of _ocr.py's "crema.client._complete" boundary). Provider/interface scaffolding reuses
the helpers from crema.test_client.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import frappe
from crema.api import transcribe
from crema.exceptions import CremaBudgetError, CremaConfigError
from crema.test_client import (
    TEST_ISOLATION_USER,
    TEST_PROVIDER,
    CremaFixtureTestCase,
    _clear_defaults,
    _ensure_interface,
    _ensure_provider,
    _ensure_user,
)

_AUDIO_BYTES = b"\x00\x01not really audio, just bytes"


class IntegrationTestCremaTranscribe(CremaFixtureTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        frappe.set_user("Administrator")
        _ensure_user(TEST_ISOLATION_USER)
        _ensure_provider()
        _ensure_interface("transcribe")
        frappe.db.commit()

    def setUp(self) -> None:
        super().setUp()
        frappe.set_user("Administrator")
        _clear_defaults()

    def test_transcribes_raw_bytes(self):
        with patch(
            "crema.client._transcribe", return_value={"text": "hello world", "language": "en", "duration": 1.2}
        ) as mock_transcribe:
            result = transcribe(_AUDIO_BYTES)

        self.assertEqual(result, {"text": "hello world", "language": "en", "duration": 1.2})
        mock_transcribe.assert_called_once()

    def test_passes_language_hint_through(self):
        with patch(
            "crema.client._transcribe", return_value={"text": "bonjour", "language": "fr", "duration": 0.9}
        ) as mock_transcribe:
            transcribe(_AUDIO_BYTES, language="fr")

        self.assertEqual(mock_transcribe.call_args.kwargs.get("language"), "fr")

    def test_unconfigured_interface_raises_config_error(self):
        settings = frappe.get_single("Crema Settings")
        row = next(r for r in settings.assignments if r.interface == "transcribe")
        row.provider = ""
        settings.save(ignore_permissions=True)

        with self.assertRaises(CremaConfigError):
            transcribe(_AUDIO_BYTES)

    def test_budget_exceeded_blocks_before_provider_call(self):
        _ensure_interface("transcribe", monthly_budget_usd=1)
        frappe.get_doc(
            {
                "doctype": "Crema Log",
                "interface": "transcribe",
                "provider": TEST_PROVIDER,
                "status": "Success",
                "cost_usd": 5,
            }
        ).insert(ignore_permissions=True)

        with patch("crema.client._transcribe") as mock_transcribe:
            with self.assertRaises(CremaBudgetError):
                transcribe(_AUDIO_BYTES)

        mock_transcribe.assert_not_called()
        log = frappe.get_last_doc("Crema Log", filters={"interface": "transcribe", "status": "Blocked"})
        self.assertTrue(log.detail)

    def test_transcribe_call_logs_a_crema_log_row(self):
        with patch("crema.client._transcribe", return_value={"text": "hi", "language": "en", "duration": 0.5}):
            transcribe(_AUDIO_BYTES)

        log = frappe.get_last_doc("Crema Log", filters={"interface": "transcribe", "status": "Success"})
        self.assertEqual(log.detail or "", "")
        self.assertTrue(log.prompt_sha)
        self.assertIsNotNone(log.duration_ms)

    def test_provider_error_logs_an_error_row_and_reraises(self):
        with patch("crema.client._transcribe", side_effect=RuntimeError("provider exploded")):
            with self.assertRaises(RuntimeError):
                transcribe(_AUDIO_BYTES)

        log = frappe.get_last_doc("Crema Log", filters={"interface": "transcribe", "status": "Error"})
        self.assertIn("provider exploded", log.detail)

    def test_cost_and_call_count_come_from_real_record_usage(self):
        """crema.client._transcribe isn't mocked away here (only litellm.transcription
        is), so client._record_usage runs for real and log.insert drains its total —
        same pattern test_ocr.py's test_escalated_ocr_row_bills_two_llm_calls pins."""
        response = MagicMock()
        response.get = lambda key, default=None: {"text": "hi", "language": "en", "duration": 1.0}.get(
            key, default
        )
        response.usage = MagicMock(prompt_tokens=0, completion_tokens=0)
        response._hidden_params = {"response_cost": 0.004}

        with patch("litellm.transcription", return_value=response):
            transcribe(_AUDIO_BYTES)

        log = frappe.get_last_doc("Crema Log", filters={"interface": "transcribe", "status": "Success"})
        self.assertEqual(log.llm_calls, 1)
        self.assertAlmostEqual(log.cost_usd, 0.004, places=6)

    def test_file_url_input_reads_via_file_manager(self):
        file_doc = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": f"_test_crema_transcribe_{uuid.uuid4().hex[:8]}.ogg",
                "content": _AUDIO_BYTES,
                "is_private": 0,
            }
        ).insert(ignore_permissions=True)

        with patch("crema.client._transcribe", return_value={"text": "from file", "language": "en", "duration": 2.0}):
            result = transcribe(file_doc.file_url)

        self.assertEqual(result["text"], "from file")
