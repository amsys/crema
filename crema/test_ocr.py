"""Integration tests for crema._ocr — zero live network calls.

Mock boundary: unittest.mock.patch("crema.client._complete"). The tiny pymupdf/PIL
documents these tests run on, and the provider/interface scaffolding, both come from
crema.test_fixtures.
"""

from __future__ import annotations

import io
import uuid
from unittest.mock import MagicMock, patch

import pymupdf
from PIL import Image as PILImage

import frappe
from crema import _ocr, cache
from crema._json import strip_fence
from crema.api import ocr
from crema.test_fixtures import (
    TEST_ISOLATION_USER,
    TEST_PROVIDER,
    CremaFixtureTestCase,
    _clear_defaults,
    _drop_advanced_ocr,
    _ensure_interface,
    _png_bytes,
    _scanned_pdf_bytes,
    _text_pdf_bytes,
)
from frappe.tests import UnitTestCase


class UnitTestCremaOcrHelpers(UnitTestCase):
    """Pure-function helpers in crema._ocr / crema._json — no frappe, no DB."""

    def test_sniff_mime_png(self):
        self.assertEqual(_ocr._sniff_mime(_png_bytes()), "image/png")

    def test_sniff_mime_jpeg(self):
        buf = io.BytesIO()
        PILImage.new("RGB", (10, 10)).save(buf, format="JPEG")
        self.assertEqual(_ocr._sniff_mime(buf.getvalue()), "image/jpeg")

    def test_sniff_mime_gif(self):
        buf = io.BytesIO()
        PILImage.new("RGB", (10, 10)).save(buf, format="GIF")
        self.assertEqual(_ocr._sniff_mime(buf.getvalue()), "image/gif")

    def test_sniff_mime_unknown_returns_empty_string(self):
        self.assertEqual(_ocr._sniff_mime(b"not a recognized format"), "")

    def test_downscale_image_shrinks_oversized_long_edge(self):
        out_data, out_mime = _ocr._downscale_image(_png_bytes((2000, 1000)), "image/png")
        img = PILImage.open(io.BytesIO(out_data))
        self.assertLessEqual(max(img.size), _ocr._MAX_SIDE_PX)
        self.assertEqual(out_mime, "image/png")

    def test_downscale_image_leaves_small_image_untouched(self):
        data = _png_bytes((100, 50))
        out_data, out_mime = _ocr._downscale_image(data, "image/png")
        self.assertEqual(out_data, data)
        self.assertEqual(out_mime, "image/png")

    def test_downscale_image_falls_back_to_original_on_corrupt_data(self):
        corrupt = b"not an image at all"
        out_data, out_mime = _ocr._downscale_image(corrupt, "image/png")
        self.assertEqual(out_data, corrupt)
        self.assertEqual(out_mime, "image/png")

    def test_prep_parts_plain_image_becomes_one_image_part(self):
        parts = _ocr.prep_parts(_png_bytes(), "image/png")
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["type"], "image_url")
        self.assertTrue(parts[0]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_prep_parts_unknown_mime_returns_empty(self):
        self.assertEqual(_ocr.prep_parts(b"random bytes", "application/octet-stream"), [])

    def test_parse_result_non_dict_json_is_treated_as_unparseable(self):
        self.assertEqual(_ocr._parse_result("[1, 2, 3]"), ("", None))

    def test_parse_result_missing_text_defaults_to_empty_string(self):
        text, confidence = _ocr._parse_result('{"confidence": 0.5}')
        self.assertEqual(text, "")
        self.assertEqual(confidence, 0.5)

    def test_parse_result_non_numeric_confidence_is_none(self):
        text, confidence = _ocr._parse_result('{"text": "hi", "confidence": "high"}')
        self.assertEqual(text, "hi")
        self.assertIsNone(confidence)

    def test_strip_fence_removes_markdown_json_fence(self):
        self.assertEqual(strip_fence('```json\n{"a": 1}\n```'), '{"a": 1}')

    def test_strip_fence_is_a_no_op_on_bare_json(self):
        self.assertEqual(strip_fence('{"a": 1}'), '{"a": 1}')


class IntegrationTestCremaOcr(CremaFixtureTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.ensure_fixtures("ocr")

    def setUp(self) -> None:
        super().setUp()
        _clear_defaults()  # else the real site's default_provider could resolve
        # "advanced_ocr" out from under test_low_confidence_no_advanced_ocr_returns_as_is
        _drop_advanced_ocr()  # every test starts with a known, uncached-stale state

    def test_text_pdf_takes_text_path(self):
        """>=100 extractable chars -> the extracted text is sent as text content, no images."""
        with patch(
            "crema.client._complete", return_value='{"text": "Hello world.", "confidence": 0.95}'
        ) as mock_complete:
            result = ocr(_text_pdf_bytes())

        self.assertEqual(result, {"text": "Hello world.", "confidence": 0.95, "escalated": False})

        messages = mock_complete.call_args[0][1]
        user_content = messages[-1]["content"]
        text_parts = [p for p in user_content if p.get("type") == "text"]
        image_parts = [p for p in user_content if p.get("type") == "image_url"]
        self.assertTrue(any("Hello world" in p["text"] for p in text_parts))
        self.assertFalse(image_parts)

    def test_scanned_pdf_produces_image_parts(self):
        """No embedded text -> pages are rendered to vision image parts."""
        canned = '{"text": "scan", "confidence": 0.9}'
        with patch("crema.client._complete", return_value=canned) as mock_complete:
            ocr(_scanned_pdf_bytes())

        messages = mock_complete.call_args[0][1]
        user_content = messages[-1]["content"]
        image_parts = [p for p in user_content if p.get("type") == "image_url"]
        self.assertTrue(image_parts)
        self.assertTrue(image_parts[0]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_low_confidence_escalates_with_advanced_ocr(self):
        """confidence < 0.7 and advanced_ocr configured -> one retry, better result wins."""
        _ensure_interface("advanced_ocr", model="test-model-advanced")
        low = '{"text": "blurry", "confidence": 0.3}'
        high = '{"text": "clear", "confidence": 0.92}'
        with patch("crema.client._complete", side_effect=[low, high]) as mock_complete:
            result = ocr(_scanned_pdf_bytes())

        self.assertEqual(mock_complete.call_count, 2)
        self.assertEqual(result, {"text": "clear", "confidence": 0.92, "escalated": True})

    def test_low_confidence_no_advanced_ocr_returns_as_is(self):
        """confidence < 0.7 and advanced_ocr NOT configured -> no retry, escalated=False."""
        low = '{"text": "blurry", "confidence": 0.3}'
        with patch("crema.client._complete", return_value=low) as mock_complete:
            result = ocr(_scanned_pdf_bytes())

        self.assertEqual(mock_complete.call_count, 1)
        self.assertEqual(result, {"text": "blurry", "confidence": 0.3, "escalated": False})

    def test_low_confidence_escalation_skipped_when_default_resolves_advanced_ocr_to_same_config(self):
        """With Crema Settings' Default Provider/Model set to exactly what "ocr" already
        used, an unconfigured "advanced_ocr" row now resolves via the default instead of
        raising CremaConfigError — but retrying the identical provider+model buys
        nothing, so _run's same-cfg guard must skip the second call."""
        settings = frappe.get_single("Crema Settings")
        settings.default_provider = TEST_PROVIDER
        settings.default_model = "test-model"
        settings.default_isolation_user = TEST_ISOLATION_USER
        settings.save(ignore_permissions=True)
        cache.clear_interfaces()

        low = '{"text": "blurry", "confidence": 0.3}'
        with patch("crema.client._complete", return_value=low) as mock_complete:
            result = ocr(_scanned_pdf_bytes())

        self.assertEqual(mock_complete.call_count, 1)
        self.assertEqual(result, {"text": "blurry", "confidence": 0.3, "escalated": False})

    def test_escalation_skipped_when_advanced_ocr_budget_exhausted(self):
        """advanced_ocr resolves and clears the same-provider/model skip (different
        model from "ocr"), but its own interface budget is already spent this month --
        _run's log.check_budget(adv_cfg) call (added alongside the same-config skip)
        must catch CremaBudgetError and serve the base (unescalated) result instead of
        making a second, budget-exceeding provider call. Same "serve what's already
        paid for" shape as the unresolvable-advanced_ocr and same-config skips above,
        for the budget ceiling instead."""
        _ensure_interface("advanced_ocr", model="test-model-advanced", monthly_budget_usd=1)
        frappe.get_doc(
            {
                "doctype": "Crema Log",
                "interface": "advanced_ocr",
                "provider": TEST_PROVIDER,
                "status": "Success",
                "cost_usd": 5,
            }
        ).insert(ignore_permissions=True)

        low = '{"text": "blurry", "confidence": 0.3}'
        with patch("crema.client._complete", return_value=low) as mock_complete:
            result = ocr(_scanned_pdf_bytes())

        self.assertEqual(mock_complete.call_count, 1)
        self.assertEqual(result, {"text": "blurry", "confidence": 0.3, "escalated": False})

        log = frappe.get_last_doc("Crema Log", filters={"interface": "ocr", "status": "Success"})
        self.assertEqual(log.status, "Success")

    def test_unparseable_json_treated_as_low_confidence(self):
        """Unparseable JSON -> confidence None, treated the same as low-confidence."""
        with patch("crema.client._complete", return_value="not json at all") as mock_complete:
            result = ocr(_scanned_pdf_bytes())

        self.assertEqual(mock_complete.call_count, 1)
        self.assertEqual(result, {"text": "", "confidence": 0.0, "escalated": False})

    # --- Crema Log audit rows ------------------------------------------------

    def test_ocr_call_logs_a_crema_log_row(self):
        """A plain (non-escalated) OCR call inserts one Crema Log row: interface "ocr",
        Success, no detail, and a prompt_sha (never the extracted text)."""
        with patch("crema.client._complete", return_value='{"text": "Hello world.", "confidence": 0.95}'):
            ocr(_text_pdf_bytes())

        log = frappe.get_last_doc("Crema Log", filters={"interface": "ocr", "status": "Success"})
        self.assertEqual(log.detail or "", "")
        self.assertTrue(log.prompt_sha)
        self.assertIsNotNone(log.duration_ms)

    def test_corrupt_pdf_logs_an_error_row(self):
        """Prep failures happen before any LLM call — they must still be audited."""
        corrupt = b"%PDF-1.4 this is not actually a pdf at all"

        with patch("crema.client._complete") as mock_complete:
            with self.assertRaises(pymupdf.FileDataError):
                ocr(corrupt)

        mock_complete.assert_not_called()
        log = frappe.get_last_doc("Crema Log", filters={"interface": "ocr", "status": "Error"})
        self.assertTrue(log.detail)

    def test_escalation_that_does_not_improve_is_logged_against_ocr(self):
        """The retry lost, so the served text came from `ocr` — the log must say so."""
        _ensure_interface("advanced_ocr", model="test-model-advanced")
        first = '{"text": "original", "confidence": 0.5}'
        worse = '{"text": "worse", "confidence": 0.2}'
        with patch("crema.client._complete", side_effect=[first, worse]) as mock_complete:
            result = ocr(_scanned_pdf_bytes())

        self.assertEqual(mock_complete.call_count, 2)
        self.assertEqual(result, {"text": "original", "confidence": 0.5, "escalated": True})

        # Filtered on interface too, not just detail="escalated": test_escalated_ocr_call_
        # logs_detail_escalated below writes a same-detail row for "advanced_ocr", and
        # get_last_doc's creation-order tiebreak between rows written microseconds apart
        # is undefined — the interface filter makes which row is fetched unambiguous.
        log = frappe.get_last_doc("Crema Log", filters={"interface": "ocr", "detail": "escalated"})
        self.assertEqual(log.interface, "ocr")

    def test_escalated_ocr_call_logs_detail_escalated(self):
        """When escalation to advanced_ocr fires, the logged row's interface is
        "advanced_ocr" and its detail is "escalated"."""
        _ensure_interface("advanced_ocr", model="test-model-advanced")
        low = '{"text": "blurry", "confidence": 0.3}'
        high = '{"text": "clear", "confidence": 0.92}'
        with patch("crema.client._complete", side_effect=[low, high]):
            ocr(_scanned_pdf_bytes())

        log = frappe.get_last_doc("Crema Log", filters={"interface": "advanced_ocr", "detail": "escalated"})
        self.assertEqual(log.status, "Success")
        self.assertTrue(log.prompt_sha)

    def test_escalated_ocr_row_bills_two_llm_calls(self):
        """An escalated OCR call makes two provider calls (base + advanced_ocr) but
        writes one Crema Log row — that row's llm_calls must say 2, not 1, or a cost
        dashboard silently undercounts every escalation. crema.client._complete isn't
        mocked away here (only litellm.completion is), so client._record_usage runs
        for real on both calls and log.insert drains their combined total."""
        _ensure_interface("advanced_ocr", model="test-model-advanced")
        low = MagicMock()
        low.choices = [MagicMock(message=MagicMock(content='{"text": "blurry", "confidence": 0.3}'))]
        low.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
        low._hidden_params = {"response_cost": 0.01}
        high = MagicMock()
        high.choices = [MagicMock(message=MagicMock(content='{"text": "clear", "confidence": 0.92}'))]
        high.usage = MagicMock(prompt_tokens=20, completion_tokens=10)
        high._hidden_params = {"response_cost": 0.02}

        with patch("litellm.completion", side_effect=[low, high]):
            ocr(_scanned_pdf_bytes())

        log = frappe.get_last_doc("Crema Log", filters={"interface": "advanced_ocr", "detail": "escalated"})
        self.assertEqual(log.llm_calls, 2)
        self.assertEqual(log.prompt_tokens, 30)
        self.assertEqual(log.completion_tokens, 15)
        self.assertAlmostEqual(log.cost_usd, 0.03, places=6)

    def test_escalation_itself_returning_unparseable_json_keeps_the_original(self):
        """adv_confidence is None (unparseable) -> the improvement check can't fire, so
        the original result is served, still marked escalated=True (an attempt was made)."""
        _ensure_interface("advanced_ocr", model="test-model-advanced")
        low = '{"text": "blurry", "confidence": 0.3}'
        with patch("crema.client._complete", side_effect=[low, "not json at all"]) as mock_complete:
            result = ocr(_scanned_pdf_bytes())

        self.assertEqual(mock_complete.call_count, 2)
        self.assertEqual(result, {"text": "blurry", "confidence": 0.3, "escalated": True})

    # --- str input is a File URL, resolved inside the isolation user's sandbox --------

    def test_file_url_input_reads_via_file_manager(self):
        file_doc = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": f"_test_crema_ocr_{uuid.uuid4().hex[:8]}.pdf",
                "content": _text_pdf_bytes(),
                "is_private": 0,
            }
        ).insert(ignore_permissions=True)

        with patch("crema.client._complete", return_value='{"text": "Hello world.", "confidence": 0.95}'):
            result = ocr(file_doc.file_url)

        self.assertEqual(result["text"], "Hello world.")

    def test_private_file_url_content_is_read_regardless_of_isolation_user_permission(self):
        """Pins a discovered gap, not intended behavior: _load_bytes calls
        frappe.utils.file_manager.get_file(), which resolves the path with a bare DB
        query and reads it straight off disk — it never calls check_permission(), unlike
        api._resolve_files's ask(files=[...]) path, which does (see IntegrationTestCremaFiles
        in test_client.py). So a private File's bytes are read here regardless of whether
        the isolation user could actually read that File document. Flagged, not fixed in
        this pass — same "known accepted risk" treatment as the automation._fetch SSRF
        gap in CLAUDE.md, but arguably more sensitive since it contradicts _load_bytes's
        own docstring claim that "private-file permissions apply" inside the sandbox."""
        file_doc = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": f"_test_crema_ocr_{uuid.uuid4().hex[:8]}.pdf",
                "content": _text_pdf_bytes(),
                "is_private": 1,
            }
        ).insert(ignore_permissions=True)

        with patch("crema.client._complete", return_value='{"text": "Hello world.", "confidence": 0.95}'):
            result = ocr(file_doc.file_url)

        self.assertEqual(result["text"], "Hello world.")

    # --- instruction reaches the OCR prompt -------------------------------------------

    def test_instruction_is_prefixed_to_the_ocr_prompt(self):
        with patch(
            "crema.client._complete", return_value='{"text": "x", "confidence": 0.9}'
        ) as mock_complete:
            ocr(_text_pdf_bytes(), instruction="Read the header carefully")

        text_part = mock_complete.call_args[0][1][-1]["content"][0]["text"]
        self.assertTrue(text_part.startswith("Read the header carefully"))
