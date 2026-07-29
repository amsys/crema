"""Integration tests for crema.automation — zero live network calls.

Mock boundary: `patch("crema.api.ask_json")` for every LLM call (planner and extractor
both go through it) and `patch("requests.get")` for the fetch. Everything else — plan
validation, the sandbox, the upsert, permission enforcement — runs for real.

`frappe.db.commit` is patched around run_task so the tasks/ToDos/Notes a test creates
stay inside the class transaction and get rolled back with it.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from crema import automation
from crema.exceptions import CremaConfigError
from crema.test_client import (
    TEST_ISOLATION_USER,
    CremaFixtureTestCase,
    _ensure_interface,
    _ensure_provider,
    _ensure_user,
)
from crema.test_ocr import _text_pdf_bytes
from frappe.utils import add_to_date, now_datetime

TEST_INTERFACE = "complex"  # a real PREDEFINED name — CremaAutomationTask now rejects any other

HTML_BODY = (
    b"<html><head><style>p{color:red}</style></head><body><p>Alpha</p><script>evil()</script></body></html>"
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _make_task(**kw):
    doc = frappe.new_doc("Crema Automation Task")
    doc.task_name = kw.pop("task_name", f"_test_crema_task_{uuid.uuid4().hex[:8]}")
    doc.enabled = kw.pop("enabled", 1)
    doc.source_url = "https://example.invalid/source"
    doc.schedule = kw.pop("schedule", "0 3 * * *")
    doc.instruction = "Extract the items and store them."
    doc.interface = kw.pop("interface", TEST_INTERFACE)
    doc.target_doctype = kw.pop("target_doctype", "ToDo")
    doc.plan_json = kw.pop("plan_json", None)
    doc.insert(ignore_permissions=True)
    return doc


def _last_error(task) -> str:
    return frappe.db.get_value("Crema Automation Task", task.name, "last_error") or ""


def _response(body: bytes = HTML_BODY, content_type: str = "text/html") -> MagicMock:
    response = MagicMock()
    response.content = body
    response.headers = {"content-type": content_type}
    response.encoding = "utf-8"
    response.raise_for_status.return_value = None
    return response


def _run(task: str, ask_results: list):
    """Run a task with canned LLM answers. Returns (status, ask_json mock)."""
    with (
        patch("requests.get", return_value=_response()),
        patch("crema.api.ask_json", side_effect=ask_results) as ask_json,
        patch("frappe.db.commit"),
    ):
        status = automation.run_task(task)
    return status, ask_json


# ---------------------------------------------------------------------------
# canned plans / rows
# ---------------------------------------------------------------------------

EXTRACT_PROMPT = "Extract every item as a row."


def _todo_plan() -> dict:
    """ToDo's has_permission hook only lets a non-System-Manager touch rows allocated to
    them, so the plan maps `allocated_to` — that is what keeps the fenced isolation user
    able to write here while still being refused on Role (see the sandbox test)."""
    return {
        "version": 1,
        "extract": {"prompt": EXTRACT_PROMPT, "response_schema": {"type": "json_object"}},
        "map": {
            "doctype": "ToDo",
            "match_fields": ["description"],
            "field_map": {"text": "description", "prio": "priority", "who": "allocated_to"},
        },
    }


def _todo_rows(marker: str) -> dict:
    return {
        "rows": [
            {"text": f"{marker} one", "prio": "Low", "who": TEST_ISOLATION_USER},
            {"text": f"{marker} two", "prio": "High", "who": TEST_ISOLATION_USER},
        ]
    }


class IntegrationTestCremaAutomation(CremaFixtureTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        frappe.set_user("Administrator")
        _ensure_user(TEST_ISOLATION_USER)
        _ensure_provider()
        _ensure_interface(TEST_INTERFACE, enable_prompt_scan=False)
        frappe.db.commit()

    def setUp(self) -> None:
        super().setUp()
        frappe.set_user("Administrator")

    def _todos(self, marker: str) -> list[str]:
        return frappe.get_all("ToDo", filters={"description": ["like", f"%{marker}%"]}, pluck="name")

    # --- fetch -----------------------------------------------------------

    def test_fetch_strips_scripts_styles_and_tags(self):
        with patch("requests.get", return_value=_response()):
            text = automation._fetch("https://example.invalid/page")

        self.assertIn("Alpha", text)
        self.assertNotIn("evil()", text)
        self.assertNotIn("color:red", text)

    def test_fetch_pdf_uses_embedded_text(self):
        with patch("requests.get", return_value=_response(_text_pdf_bytes(), "application/pdf")):
            text = automation._fetch("https://example.invalid/doc.pdf")

        self.assertIn("Hello world", text)

    def test_fetch_scanned_pdf_routes_through_ocr(self):
        from crema.test_ocr import _scanned_pdf_bytes

        ocr_result = {"text": "ocr extracted text", "confidence": 0.9, "escalated": False}
        with (
            patch("requests.get", return_value=_response(_scanned_pdf_bytes(), "application/pdf")),
            patch("crema.api.ocr", return_value=ocr_result) as mock_ocr,
        ):
            text = automation._fetch("https://example.invalid/scan.pdf")

        self.assertEqual(text, "ocr extracted text")
        mock_ocr.assert_called_once()

    def test_fetch_plain_text_body_used_as_is(self):
        with patch("requests.get", return_value=_response(b"just plain text", "text/plain")):
            text = automation._fetch("https://example.invalid/plain.txt")

        self.assertEqual(text, "just plain text")

    def test_fetch_sniffs_mime_when_content_type_header_is_missing(self):
        with patch("requests.get", return_value=_response(_text_pdf_bytes(), "")):
            text = automation._fetch("https://example.invalid/doc")

        self.assertIn("Hello world", text)

    def test_fetch_raise_for_status_failure_records_a_failed_run(self):
        bad_response = MagicMock()
        bad_response.raise_for_status.side_effect = Exception("503 Service Unavailable")
        task = _make_task()

        with patch("requests.get", return_value=bad_response), patch("frappe.db.commit"):
            status = automation.run_task(task.name)

        self.assertEqual(status, "Failed")
        task.reload()
        self.assertIn("fetch failed", task.last_error)

    # --- first run: plan + upsert ----------------------------------------

    def test_first_run_plans_and_upserts(self):
        marker = uuid.uuid4().hex[:10]
        task = _make_task()

        status, ask_json = _run(task.name, [_todo_plan(), _todo_rows(marker)])

        self.assertEqual(status, "Success")
        self.assertEqual(ask_json.call_count, 2)  # plan, then extract

        task.reload()
        self.assertEqual(task.last_status, "Success")
        self.assertEqual(task.consecutive_failures, 0)
        self.assertTrue(task.last_run)

        stored = frappe.parse_json(task.plan_json)
        self.assertEqual(stored["map"]["doctype"], "ToDo")
        self.assertEqual(stored["extract"]["prompt"], EXTRACT_PROMPT)

        self.assertEqual(len(self._todos(marker)), 2)
        todo = frappe.get_doc("ToDo", self._todos(marker)[0])
        self.assertIn(marker, todo.description)
        self.assertEqual(todo.owner, TEST_ISOLATION_USER)

    # --- second run: stored plan reused, upsert idempotent ----------------

    def test_second_run_reuses_plan_and_does_not_duplicate(self):
        marker = uuid.uuid4().hex[:10]
        task = _make_task()
        _run(task.name, [_todo_plan(), _todo_rows(marker)])

        status, ask_json = _run(task.name, [_todo_rows(marker)])

        self.assertEqual(status, "Success")
        self.assertEqual(ask_json.call_count, 1)  # planner NOT called again
        self.assertTrue(ask_json.call_args[0][1].startswith(EXTRACT_PROMPT))
        self.assertEqual(len(self._todos(marker)), 2)

    # --- replan ------------------------------------------------------------

    def test_extraction_failure_triggers_one_replan(self):
        marker = uuid.uuid4().hex[:10]
        task = _make_task(plan_json=frappe.as_json(_todo_plan()))

        status, ask_json = _run(task.name, [{"rows": []}, _todo_plan(), _todo_rows(marker)])

        self.assertEqual(status, "Replanned")
        self.assertEqual(ask_json.call_count, 3)

        replan_prompt = ask_json.call_args_list[1][0][1]
        self.assertIn("FAILED", replan_prompt)
        self.assertIn("no usable rows", replan_prompt)

        task.reload()
        self.assertEqual(task.last_status, "Replanned")
        self.assertEqual(task.consecutive_failures, 0)
        self.assertEqual(len(self._todos(marker)), 2)

    def test_plan_with_unknown_key_is_rejected_and_replanned(self):
        marker = uuid.uuid4().hex[:10]
        bad_plan = {**_todo_plan(), "code": "import os; os.system('rm -rf /')"}
        task = _make_task()

        status, ask_json = _run(task.name, [bad_plan, _todo_plan(), _todo_rows(marker)])

        self.assertEqual(status, "Replanned")
        self.assertEqual(ask_json.call_count, 3)
        self.assertIn("unknown keys", ask_json.call_args_list[1][0][1])

        task.reload()
        self.assertNotIn("code", frappe.parse_json(task.plan_json))

    # --- hard failure ------------------------------------------------------

    def test_bogus_fieldname_fails_both_attempts(self):
        bad_plan = _todo_plan()
        bad_plan["map"]["field_map"] = {"text": "not_a_real_todo_field"}
        bad_plan["map"]["match_fields"] = ["not_a_real_todo_field"]
        task = _make_task()

        status, ask_json = _run(task.name, [bad_plan, bad_plan])

        self.assertEqual(status, "Failed")
        self.assertEqual(ask_json.call_count, 2)

        task.reload()
        self.assertEqual(task.last_status, "Failed")
        self.assertEqual(task.consecutive_failures, 1)
        self.assertIn("not_a_real_todo_field", task.last_error)
        self.assertFalse(task.plan_json)

    def test_five_consecutive_failures_disable_the_task(self):
        task = _make_task()
        bad_plan = {"version": 1}  # missing extract/map

        with patch("frappe.log_error") as log_error:
            for _ in range(5):
                _run(task.name, [bad_plan, bad_plan])

        task.reload()
        self.assertEqual(task.consecutive_failures, 5)
        self.assertEqual(task.enabled, 0)
        self.assertEqual(task.last_status, "Failed")
        log_error.assert_called_once()

    # --- sandbox enforcement ------------------------------------------------

    def test_upsert_without_permission_fails_the_task(self):
        """The isolation user has no Role permissions -> doc.save() raises PermissionError,
        proving no ignore_permissions leaked into the upsert path."""
        plan = {
            "version": 1,
            "extract": {"prompt": EXTRACT_PROMPT},
            "map": {
                "doctype": "Role",
                "match_fields": ["role_name"],
                "field_map": {"role": "role_name"},
            },
        }
        rows = {"rows": [{"role": f"_test_crema_role_{uuid.uuid4().hex[:8]}"}]}
        task = _make_task(target_doctype="Role", plan_json=frappe.as_json(plan))

        status, _ = _run(task.name, [rows, plan, rows])

        self.assertEqual(status, "Failed")
        task.reload()
        self.assertIn("PermissionError", task.last_error)

    def test_unresolvable_interface_is_recorded_as_a_failure(self):
        """A disabled provider used to raise out of run_task entirely, leaving
        last_status stale and never tripping the auto-disable counter."""
        task = _make_task()

        with (
            patch("requests.get", return_value=_response()),
            patch("crema.api.ask_json") as ask_json,
            patch("frappe.db.commit"),
            patch("crema.client._resolve", side_effect=CremaConfigError("provider disabled")),
        ):
            status = automation.run_task(task.name)

        self.assertEqual(status, "Failed")
        ask_json.assert_not_called()

        task.reload()
        self.assertEqual(task.last_status, "Failed")
        self.assertEqual(task.consecutive_failures, 1)
        self.assertIn("CremaConfigError", task.last_error)

    # --- upsert lookups are permission-fenced --------------------------------

    def test_upsert_does_not_match_records_the_isolation_user_cannot_see(self):
        """The find-or-create lookup runs as the isolation user (get_list, not get_all),
        so a ToDo it cannot read is never located, loaded or overwritten."""
        marker = uuid.uuid4().hex[:10]
        hidden = frappe.get_doc(
            {
                "doctype": "ToDo",
                "description": f"{marker} one",
                "priority": "Medium",
                "allocated_to": "Administrator",
            }
        ).insert(ignore_permissions=True)

        task = _make_task(plan_json=frappe.as_json(_todo_plan()))
        status, _ = _run(task.name, [{"rows": [{"text": f"{marker} one", "who": TEST_ISOLATION_USER}]}])

        self.assertEqual(status, "Success")
        self.assertEqual(len(self._todos(marker)), 2)  # a new one, not an overwrite

        hidden.reload()
        self.assertEqual(hidden.allocated_to, "Administrator")
        self.assertEqual(hidden.priority, "Medium")

    # --- child tables -------------------------------------------------------

    def test_child_rows_upserted_and_deduplicated(self):
        """Contact, not Note: every Contact field sits at permlevel 0 and the "All" role
        may create/write its own, so this runs under a genuinely fenced isolation user."""
        name = f"_test_crema_contact_{uuid.uuid4().hex[:8]}"
        plan = {
            "version": 1,
            "extract": {"prompt": EXTRACT_PROMPT},
            "map": {
                "doctype": "Contact",
                "match_fields": ["first_name"],
                "field_map": {"t": "first_name"},
                "child_table": {
                    "fieldname": "email_ids",
                    "match_fields": ["email_id"],
                    "field_map": {"e": "email_id"},
                },
            },
        }
        rows = {
            "rows": [
                {"t": name, "e": f"{name}.a@example.com"},
                {"t": name, "e": f"{name}.b@example.com"},
            ]
        }
        task = _make_task(target_doctype="Contact", plan_json=frappe.as_json(plan))

        self.assertEqual(_run(task.name, [rows])[0], "Success", _last_error(task))
        contact = frappe.get_doc("Contact", {"first_name": name})
        self.assertEqual(len(contact.email_ids), 2)

        self.assertEqual(_run(task.name, [rows])[0], "Success", _last_error(task))
        contact.reload()
        self.assertEqual(len(contact.email_ids), 2)  # matched, not appended again

    # --- tick due-logic -----------------------------------------------------

    def test_tick_enqueues_only_due_tasks(self):
        fresh = _make_task(schedule="0 3 * * *")
        fresh.db_set("last_run", now_datetime())
        due = _make_task(schedule="0 3 * * *")
        due.db_set("last_run", add_to_date(now_datetime(), days=-2))

        with patch("frappe.enqueue") as enqueue:
            automation.tick()

        queued = {call.kwargs.get("task") for call in enqueue.call_args_list}
        self.assertIn(due.name, queued)
        self.assertNotIn(fresh.name, queued)

    def test_disabled_task_is_never_enqueued(self):
        task = _make_task(enabled=0)
        task.db_set("last_run", add_to_date(now_datetime(), days=-2))

        with patch("frappe.enqueue") as enqueue:
            automation.tick()

        self.assertNotIn(task.name, {call.kwargs.get("task") for call in enqueue.call_args_list})

    def test_tick_skips_a_task_whose_stored_cron_is_no_longer_valid(self):
        """CremaAutomationTask.validate() blocks a bad cron on save, but tick() must
        still degrade gracefully rather than exploding if one somehow got stored."""
        task = _make_task(schedule="0 3 * * *")
        task.db_set("last_run", add_to_date(now_datetime(), days=-2))
        frappe.db.set_value("Crema Automation Task", task.name, "schedule", "not a cron expression")

        with patch("frappe.enqueue") as enqueue:
            automation.tick()  # must not raise

        self.assertNotIn(task.name, {call.kwargs.get("task") for call in enqueue.call_args_list})

    # --- stored plan re-validated on load ------------------------------------

    def test_stored_plan_is_absent_after_target_doctype_changes_underneath_it(self):
        task = _make_task(plan_json=frappe.as_json(_todo_plan()))  # plan's map.doctype == "ToDo"
        task.db_set("target_doctype", "Contact")
        task.reload()

        self.assertIsNone(automation._stored_plan(task))

    # --- _validate_plan branches ---------------------------------------------

    def test_validate_plan_rejects_blank_extract_prompt(self):
        plan = _todo_plan()
        plan["extract"]["prompt"] = "   "
        with self.assertRaises(automation.AutomationError):
            automation._validate_plan(plan, "ToDo")

    def test_validate_plan_rejects_non_object_response_schema(self):
        plan = _todo_plan()
        plan["extract"]["response_schema"] = "not an object"
        with self.assertRaises(automation.AutomationError):
            automation._validate_plan(plan, "ToDo")

    def test_validate_plan_rejects_a_nonexistent_doctype(self):
        plan = _todo_plan()
        plan["map"]["doctype"] = "_Not A Real DocType"
        with self.assertRaises(automation.AutomationError):
            automation._validate_plan(plan, None)

    def test_validate_plan_rejects_doctype_that_does_not_match_the_task_target(self):
        """The fence that stops a self-written plan redirecting writes to a different
        doctype than the one a System Manager configured on the task."""
        plan = _todo_plan()  # map.doctype == "ToDo"
        with self.assertRaises(automation.AutomationError):
            automation._validate_plan(plan, "Contact")

    def test_validate_plan_rejects_child_table_fieldname_that_is_not_a_table_field(self):
        plan = _todo_plan()
        plan["map"]["child_table"] = {
            "fieldname": "description",  # a real ToDo field, but fieldtype Text, not Table
            "match_fields": ["x"],
            "field_map": {"x": "y"},
        }
        with self.assertRaises(automation.AutomationError):
            automation._validate_plan(plan, "ToDo")

    # --- _validate_field_maps branches ----------------------------------------

    def test_validate_field_maps_rejects_empty_field_map(self):
        meta = frappe.get_meta("ToDo")
        with self.assertRaises(automation.AutomationError):
            automation._validate_field_maps(["description"], {}, meta, "plan.map")

    def test_validate_field_maps_rejects_empty_match_fields(self):
        meta = frappe.get_meta("ToDo")
        with self.assertRaises(automation.AutomationError):
            automation._validate_field_maps([], {"text": "description"}, meta, "plan.map")

    def test_validate_field_maps_rejects_non_string_key_or_value(self):
        meta = frappe.get_meta("ToDo")
        with self.assertRaises(automation.AutomationError):
            automation._validate_field_maps(["description"], {"text": 123}, meta, "plan.map")

    def test_validate_field_maps_rejects_a_match_field_absent_from_field_map(self):
        """No filter could ever be built from a match field the field map never fills."""
        meta = frappe.get_meta("ToDo")
        with self.assertRaises(automation.AutomationError):
            automation._validate_field_maps(["priority"], {"text": "description"}, meta, "plan.map")

    # --- _upsert / _upsert_child edge cases -----------------------------------

    def test_upsert_skips_a_row_that_cannot_be_identified(self):
        """The match field's fieldname isn't populated by this row's field_map, so the
        lookup filter would be {"description": None} — skipped rather than creating a
        junk record."""
        mapping = {
            "doctype": "ToDo",
            "match_fields": ["description"],
            "field_map": {"text": "description", "who": "allocated_to"},
        }
        rows = [{"who": TEST_ISOLATION_USER}]  # no "text" key -> "description" unpopulated
        before = frappe.db.count("ToDo")

        automation._upsert(mapping, rows)

        self.assertEqual(frappe.db.count("ToDo"), before)

    def test_upsert_child_does_nothing_when_the_row_has_no_mapped_values(self):
        doc = frappe.new_doc("Contact")
        doc.first_name = f"_test_crema_child_{uuid.uuid4().hex[:8]}"
        doc.insert(ignore_permissions=True)
        child = {
            "fieldname": "email_ids",
            "match_fields": ["email_id"],
            "field_map": {"missing_key": "email_id"},
        }

        automation._upsert_child(doc, child, {"unrelated_key": "value"})  # "missing_key" absent from row

        self.assertEqual(len(doc.email_ids), 0)

    # --- _meta_summary (now living in api.py) ---------------------------------

    def test_meta_summary_includes_child_table_fields(self):
        from crema.api import _meta_summary

        summary = _meta_summary("Contact")
        self.assertIn("Fields of doctype 'Contact'", summary)
        self.assertIn("first_name", summary)
        self.assertIn("Fields of child table 'email_ids'", summary)
        self.assertIn("email_id", summary)

    # --- log retention --------------------------------------------------------

    def test_cleanup_logs_deletes_old_rows_and_keeps_recent_ones(self):
        old_log = frappe.get_doc({"doctype": "Crema Log", "interface": "simple", "status": "Success"}).insert(
            ignore_permissions=True
        )
        frappe.db.set_value("Crema Log", old_log.name, "creation", add_to_date(now_datetime(), days=-40))
        recent_log = frappe.get_doc(
            {"doctype": "Crema Log", "interface": "simple", "status": "Success"}
        ).insert(ignore_permissions=True)

        automation.cleanup_logs()

        self.assertFalse(frappe.db.exists("Crema Log", old_log.name))
        self.assertTrue(frappe.db.exists("Crema Log", recent_log.name))

    # --- Run Now endpoint ---------------------------------------------------

    def test_run_automation_now_enqueues(self):
        """job_id falls back to the deterministic f-string only when frappe.enqueue's
        return value has no usable .id (e.g. mocked as None, or RQ running in sync mode).
        The real `job.id` branch (enqueue.automation.py:113) is exercised separately
        below."""
        from crema.api import run_automation_now

        task = _make_task()
        with patch("frappe.enqueue", return_value=None) as enqueue:
            job_id = run_automation_now(task.name)

        self.assertEqual(job_id, f"crema-task-{task.name}")
        enqueue.assert_called_once_with(
            "crema.automation.run_task",
            task=task.name,
            queue="long",
            timeout=1800,
            job_id=f"crema-task-{task.name}",
            deduplicate=True,
        )

    def test_run_automation_now_returns_the_real_job_id_when_available(self):
        """When frappe.enqueue returns a job object with an .id (the real RQ return
        value), that id is used instead of the job_id fallback."""
        from crema.api import run_automation_now

        task = _make_task()
        with patch("frappe.enqueue", return_value=SimpleNamespace(id="rq-job-abc123")) as enqueue:
            job_id = run_automation_now(task.name)

        self.assertEqual(job_id, "rq-job-abc123")
        enqueue.assert_called_once()

    def test_run_automation_now_rejects_unknown_task(self):
        from crema.api import run_automation_now

        with self.assertRaises(frappe.ValidationError):
            run_automation_now("_no_such_crema_task")
