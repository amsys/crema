"""Integration tests for crema.api.transcribe — zero live network calls.

Mock boundary: unittest.mock.patch("crema.client._transcribe") (transcribe()'s sibling
of _ocr.py's "crema.client._complete" boundary). Provider/interface scaffolding reuses
the helpers from crema.test_client.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import frappe
from crema import client
from crema.api import transcribe
from crema.exceptions import CremaBudgetError, CremaConfigError
from crema.test_fixtures import (
    TEST_PROVIDER,
    CremaFixtureTestCase,
    _clear_defaults,
    _ensure_interface,
)

_AUDIO_BYTES = b"\x00\x01not really audio, just bytes"


class IntegrationTestCremaTranscribe(CremaFixtureTestCase):
    """crema.api.transcribe — the audio verb, mirroring ocr()'s config/budget/log
    plumbing but calling client._transcribe instead of client._complete."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.ensure_fixtures("transcribe")

    def setUp(self) -> None:
        super().setUp()
        _clear_defaults()

    def test_raw_audio_bytes_return_the_transcript_text(self):
        with patch(
            "crema.client._transcribe",
            return_value={"text": "hello world", "language": "en", "duration": 1.2},
        ) as mock_transcribe:
            result = transcribe(_AUDIO_BYTES)

        self.assertEqual(result, {"text": "hello world", "language": "en", "duration": 1.2})
        mock_transcribe.assert_called_once()

    def test_transcribe_passes_the_language_kwarg_through_to_client_transcribe(self):
        """Sibling of test_transcribe_passes_language_to_litellm_when_given below, one
        layer up: this mocks client._transcribe itself, proving api.transcribe forwards
        the kwarg; that test mocks litellm.transcription, proving client._transcribe
        forwards it the rest of the way."""
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
        with patch(
            "crema.client._transcribe", return_value={"text": "hi", "language": "en", "duration": 0.5}
        ):
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

    def test_transcribe_builds_expected_litellm_kwargs(self):
        """crema.client._transcribe's kwargs shape -- _transcribe's sibling of
        test_client.py's test_complete_builds_expected_litellm_kwargs (which pins
        _complete's kwargs the same way). Only litellm.transcription is mocked, not
        client._transcribe itself, so api.transcribe's filename-from-mime derivation
        (api.py ~line 469-476) is exercised along with _transcribe's own kwargs.

        _AUDIO_BYTES matches none of _ocr._sniff_mime's magic-byte signatures, so mime
        sniffs to "" -- deterministically the simplest case: mimetypes.guess_extension("")
        is None, so the filename stays bare "audio" and the mime arg falls back to
        "application/octet-stream", both asserted below."""
        cfg = client._resolve("transcribe")
        response = MagicMock()
        response.get = lambda key, default=None: {"text": "hi", "language": None, "duration": None}.get(
            key, default
        )
        response.usage = None
        response._hidden_params = {}

        with patch("litellm.transcription", return_value=response) as mock_transcription:
            transcribe(_AUDIO_BYTES)

        kwargs = mock_transcription.call_args.kwargs
        self.assertEqual(kwargs["model"], f"openai/{cfg['model']}")
        self.assertEqual(kwargs["file"], ("audio", _AUDIO_BYTES, "application/octet-stream"))
        self.assertEqual(kwargs["api_base"], cfg["base_url"])
        self.assertEqual(kwargs["api_key"], client._api_key(cfg["provider"]))
        self.assertEqual(kwargs["timeout"], cfg["timeout_seconds"])
        self.assertEqual(kwargs["response_format"], "verbose_json")
        self.assertEqual(kwargs["user"], frappe.session.user)
        self.assertEqual(kwargs["metadata"], {"tags": [cfg["interface"]]})
        self.assertNotIn("language", kwargs)

    def test_transcribe_filename_carries_an_extension_for_a_known_mime(self):
        """Whisper-style endpoints commonly derive the audio format from the multipart
        filename's extension, not (only) the content type — api.transcribe derives it
        from the mime via mimetypes.guess_extension. Regression test: the filename
        used to be the bare, extension-less literal "audio" for every mime."""
        import mimetypes

        response = MagicMock()
        response.get = lambda key, default=None: {"text": "hi", "language": None, "duration": None}.get(
            key, default
        )
        response.usage = None
        response._hidden_params = {}

        with (
            patch("crema._ocr._load_bytes", return_value=(_AUDIO_BYTES, "audio/mpeg")),
            patch("litellm.transcription", return_value=response) as mock_transcription,
        ):
            transcribe(_AUDIO_BYTES)

        filename, audio, mime = mock_transcription.call_args.kwargs["file"]
        self.assertEqual(mime, "audio/mpeg")
        self.assertEqual(audio, _AUDIO_BYTES)
        self.assertRegex(filename, r"^audio\.\w+$")
        self.assertEqual(filename, "audio" + mimetypes.guess_extension("audio/mpeg"))

    def test_transcribe_passes_language_to_litellm_when_given(self):
        """Sibling of the kwargs test above, isolating the one conditional key:
        "language" must reach litellm.transcription's kwargs when the caller passes
        one, and be absent (proven above) when it doesn't."""
        response = MagicMock()
        response.get = lambda key, default=None: {"text": "hola", "language": "es", "duration": 1.0}.get(
            key, default
        )
        response.usage = None
        response._hidden_params = {}

        with patch("litellm.transcription", return_value=response) as mock_transcription:
            transcribe(_AUDIO_BYTES, language="es")

        self.assertEqual(mock_transcription.call_args.kwargs["language"], "es")

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

    def test_file_url_input_reads_the_file_document(self):
        file_doc = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": f"_test_crema_transcribe_{uuid.uuid4().hex[:8]}.ogg",
                "content": _AUDIO_BYTES,
                "is_private": 0,
            }
        ).insert(ignore_permissions=True)

        with patch(
            "crema.client._transcribe", return_value={"text": "from file", "language": "en", "duration": 2.0}
        ):
            result = transcribe(file_doc.file_url)

        self.assertEqual(result["text"], "from file")
