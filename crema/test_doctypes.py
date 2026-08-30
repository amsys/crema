"""Integration tests for the crema doctype controllers not already covered by
test_client.py's IntegrationTestCremaModelAssignmentValidation, plus install.py's
seeding, crema_provider.get_presets, and crema.log.insert's failure-degrades-quietly
path.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import frappe
from crema import api, cache, client, guardrails, interfaces, log, policy, testing
from crema.crema.doctype.crema_health_word.crema_health_word import translate_words
from crema.crema.doctype.crema_provider.crema_provider import (
    PRESETS,
    _is_local_or_private,
    _settings_references,
    create_from_template,
    get_presets,
)
from crema.exceptions import CremaConfigError
from crema.test_fixtures import (
    TEST_ISOLATION_USER,
    TEST_PLAIN_USER,
    TEST_PROVIDER,
    CremaFixtureTestCase,
    _clear_defaults,
    _ensure_provider,
    _ensure_user,
    _set_guardrail,
)
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import validate_email_address


class UnitTestCremaIsLocalOrPrivate(UnitTestCase):
    """Pure function, no frappe I/O."""

    def test_localhost_suffix_is_local(self):
        self.assertTrue(_is_local_or_private("http://localhost:11434/v1"))
        self.assertTrue(_is_local_or_private("http://myollama.localhost/v1"))

    def test_private_ipv4_is_local(self):
        self.assertTrue(_is_local_or_private("http://192.168.1.10:11434/v1"))
        self.assertTrue(_is_local_or_private("http://10.0.0.5/v1"))

    def test_loopback_ipv4_is_local(self):
        self.assertTrue(_is_local_or_private("http://127.0.0.1:11434/v1"))

    def test_private_ipv6_is_local(self):
        self.assertTrue(_is_local_or_private("http://[fd00::1]/v1"))

    def test_public_hostname_is_not_local(self):
        self.assertFalse(_is_local_or_private("https://api.openai.com/v1"))

    def test_bare_hostname_that_is_not_an_ip_is_not_local(self):
        """ipaddress.ip_address raises ValueError on a hostname — caught, not local."""
        self.assertFalse(_is_local_or_private("https://internal-llm-host/v1"))

    def test_empty_base_url_is_not_local(self):
        self.assertFalse(_is_local_or_private(""))


class UnitTestCremaRefreshViewPromptPatch(UnitTestCase):
    """Pure string comparison, no frappe I/O. Guards patches.refresh_view_prompt's own
    doc comment: OLD_PROMPTS must be appended to on every future edit to
    DEFAULT_PROMPTS["view"], or the patch silently stops matching stale stored prompts
    on sites installed after that edit."""

    def test_current_default_is_not_among_the_superseded_prompts(self):
        from crema.patches import refresh_view_prompt

        self.assertNotIn(interfaces.DEFAULT_PROMPTS["view"], refresh_view_prompt.OLD_PROMPTS)


class IntegrationTestCremaProvider(CremaFixtureTestCase):
    """CremaProvider.validate/on_update/on_trash, get_presets, and the templates."""

    def test_enabled_public_provider_without_api_key_is_rejected(self):
        doc = frappe.new_doc("Crema Provider")
        doc.provider_name = f"_test_crema_provider_{uuid.uuid4().hex[:8]}"
        doc.base_url = "https://api.openai.com/v1"
        doc.enabled = 1
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_enabled_local_provider_without_api_key_is_accepted(self):
        doc = frappe.new_doc("Crema Provider")
        doc.provider_name = f"_test_crema_provider_{uuid.uuid4().hex[:8]}"
        doc.base_url = "http://localhost:11434/v1"
        doc.enabled = 1
        doc.insert(ignore_permissions=True)  # must not raise
        try:
            self.assertTrue(frappe.db.exists("Crema Provider", doc.name))
        finally:
            frappe.delete_doc("Crema Provider", doc.name, ignore_permissions=True, force=True)

    def test_on_trash_clears_every_cached_interface_config(self):
        """cache.clear_provider nukes every crema:iface:* key wholesale (few providers,
        cheap to rebuild — see cache.py), regardless of which provider was touched."""
        key = cache.interface_key("_test_crema_probe")
        frappe.cache.set_value(key, {"fake": "cfg"})

        doc = frappe.new_doc("Crema Provider")
        doc.provider_name = f"_test_crema_provider_trash_{uuid.uuid4().hex[:8]}"
        doc.base_url = "http://localhost:11434/v1"
        doc.enabled = 1
        doc.insert(ignore_permissions=True)
        doc.delete()

        self.assertIsNone(frappe.cache.get_value(key))

    def test_get_presets_returns_the_template_dict(self):
        self.assertEqual(get_presets(), PRESETS)
        self.assertIn("OpenAI", PRESETS)
        self.assertIn("Ollama", PRESETS)

    def test_every_preset_url_is_https_or_a_private_address(self):
        from urllib.parse import urlparse

        for label, base_url in PRESETS.items():
            parsed = urlparse(base_url)
            self.assertTrue(parsed.scheme and parsed.hostname, f"{label}: not a URL: {base_url}")
            if parsed.scheme != "https":
                self.assertTrue(
                    _is_local_or_private(base_url),
                    f"{label}: non-https URL must be localhost/private: {base_url}",
                )

    def test_get_presets_rejects_non_system_manager(self):
        _ensure_user(TEST_PLAIN_USER)
        frappe.set_user(TEST_PLAIN_USER)
        with self.assertRaises(frappe.PermissionError):
            get_presets()

    def test_create_from_template_creates_and_sets_default(self):
        from crema.crema.doctype.crema_provider.crema_provider import create_from_template

        # set_as_default saves Crema Settings, and the provider/isolation pairing rule
        # refuses a Default Provider with no isolation user anywhere — a genuinely
        # fresh site has none, so provide the precondition instead of borrowing it
        # from whatever the site happens to hold. The per-test savepoint undoes it.
        if not frappe.get_single("Crema Settings").default_isolation_user:
            _ensure_user(TEST_ISOLATION_USER)
            frappe.db.set_single_value("Crema Settings", "default_isolation_user", TEST_ISOLATION_USER)
            frappe.clear_document_cache("Crema Settings")

        name = f"_test_crema_wizard_{uuid.uuid4().hex[:8]}"
        try:
            result = create_from_template(
                preset="Ollama",
                provider_name=name,
                base_url="http://localhost:11434/v1",
                enabled="1",  # the wire value a Check field actually sends — pins the
                set_as_default="1",  # frappe.utils.sbool coercion in create_from_template
            )
            self.assertEqual(result, name)
            self.assertEqual(frappe.db.get_value("Crema Provider", name, "enabled"), 1)
            self.assertEqual(frappe.get_single("Crema Settings").default_provider, name)
        finally:
            settings = frappe.get_single("Crema Settings")
            settings.default_provider = ""
            settings.save(ignore_permissions=True)
            if frappe.db.exists("Crema Provider", name):
                frappe.delete_doc("Crema Provider", name, ignore_permissions=True, force=True)

    def test_create_from_template_enabled_string_zero_is_treated_as_disabled(self):
        """The exact footgun frappe.utils.sbool guards against: a plain `bool` param on
        a whitelisted method with `from __future__ import annotations` gets no
        automatic string coercion, and the non-empty string "0" is truthy in Python."""
        from crema.crema.doctype.crema_provider.crema_provider import create_from_template

        name = f"_test_crema_wizard_disabled_{uuid.uuid4().hex[:8]}"
        try:
            create_from_template(
                preset="Ollama",
                provider_name=name,
                base_url="http://localhost:11434/v1",
                enabled="0",
                set_as_default="0",
            )
            self.assertEqual(frappe.db.get_value("Crema Provider", name, "enabled"), 0)
        finally:
            if frappe.db.exists("Crema Provider", name):
                frappe.delete_doc("Crema Provider", name, ignore_permissions=True, force=True)

    def test_create_from_template_rejects_unknown_preset(self):
        from crema.crema.doctype.crema_provider.crema_provider import create_from_template

        with self.assertRaises(frappe.ValidationError):
            create_from_template(preset="NotARealPreset", provider_name="x", base_url="http://x")

    def test_create_from_template_rejects_non_system_manager(self):
        from crema.crema.doctype.crema_provider.crema_provider import create_from_template

        _ensure_user(TEST_PLAIN_USER)
        frappe.set_user(TEST_PLAIN_USER)
        with self.assertRaises(frappe.PermissionError):
            create_from_template(preset="Ollama", provider_name="x", base_url="http://x")


class IntegrationTestCremaHealthWord(CremaFixtureTestCase):
    """CremaHealthWord.on_update/on_trash cache invalidation, and translate_words —
    the model call is mocked at crema.api.ask_json, the same boundary every other
    test in this suite mocks at."""

    def _add_word(self, word: str, enabled: int = 1, language: str = ""):
        doc = frappe.get_doc(
            {"doctype": "Crema Health Word", "word": word, "enabled": enabled, "language": language}
        )
        doc.insert()
        self.addCleanup(cache.clear_health_words)
        return doc

    def test_on_update_clears_the_cached_word_list(self):
        frappe.cache.set_value(cache.health_words_key(), ["stale"])
        self._add_word(f"_test_crema_word_{uuid.uuid4().hex[:8]}")
        self.assertIsNone(frappe.cache.get_value(cache.health_words_key()))

    def test_on_trash_clears_the_cached_word_list(self):
        doc = self._add_word(f"_test_crema_word_{uuid.uuid4().hex[:8]}")
        frappe.cache.set_value(cache.health_words_key(), ["stale"])
        doc.delete()
        self.assertIsNone(frappe.cache.get_value(cache.health_words_key()))

    def test_translate_words_rejects_non_system_manager(self):
        _ensure_user(TEST_PLAIN_USER)
        frappe.set_user(TEST_PLAIN_USER)
        with self.assertRaises(frappe.PermissionError):
            translate_words("fr")

    @patch("crema.crema.doctype.crema_health_word.crema_health_word.ask_json")
    def test_translate_words_files_new_words_switched_off(self, mock_ask_json):
        word = f"_test_crema_translated_{uuid.uuid4().hex[:6]}"
        mock_ask_json.return_value = {"words": [word]}
        self.addCleanup(
            lambda: frappe.delete_doc("Crema Health Word", word, ignore_permissions=True, force=True)
        )

        result = translate_words("fr")

        self.assertEqual(result, {"added": 1, "skipped": 0})
        doc = frappe.get_doc("Crema Health Word", word)
        self.assertEqual(doc.enabled, 0)
        self.assertEqual(doc.language, "fr")

    @patch("crema.crema.doctype.crema_health_word.crema_health_word.ask_json")
    def test_translate_words_skips_a_word_already_on_the_list(self, mock_ask_json):
        existing = self._add_word(f"_test_crema_existing_{uuid.uuid4().hex[:6]}")
        mock_ask_json.return_value = {"words": [existing.word.upper()]}

        result = translate_words("fr")

        self.assertEqual(result, {"added": 0, "skipped": 1})

    @patch("crema.crema.doctype.crema_health_word.crema_health_word.ask_json")
    def test_translate_words_rejects_a_reply_that_is_not_json(self, mock_ask_json):
        mock_ask_json.side_effect = ValueError("not json")
        with self.assertRaises(frappe.ValidationError):
            translate_words("fr")


class IntegrationTestCremaAutomationTaskValidation(IntegrationTestCase):
    """CremaAutomationTask.validate — the per-save fences: trigger, sources, action,
    match_on, and cron expression."""

    def setUp(self) -> None:
        super().setUp()
        frappe.set_user("Administrator")

    def test_invalid_cron_expression_is_rejected(self):
        doc = frappe.new_doc("Crema Automation Task")
        doc.task_name = f"_test_crema_task_{uuid.uuid4().hex[:8]}"
        doc.append("sources", {"source_type": "URL", "source_url": "https://example.invalid/source"})
        doc.schedule = "not a cron expression"
        doc.instruction = "do something"
        doc.target_doctype = "ToDo"
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_unknown_interface_name_is_rejected(self):
        """interface is a plain Autocomplete now (client-populated from
        crema.api.get_interfaces, not a Link) — validate() is what actually enforces
        it's one of interfaces.PREDEFINED."""
        doc = frappe.new_doc("Crema Automation Task")
        doc.task_name = f"_test_crema_task_{uuid.uuid4().hex[:8]}"
        doc.append("sources", {"source_type": "URL", "source_url": "https://example.invalid/source"})
        doc.schedule = "0 3 * * *"
        doc.instruction = "do something"
        doc.target_doctype = "ToDo"
        doc.interface = "_not_a_real_interface"
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_internal_interface_is_rejected_by_the_select_options(self):
        """interfaces.selectable() (names() minus INTERNAL) is stamped onto the field's
        Select options by install.sync_interface_options — and frappe validates Select
        options server-side on every save (_validate_selects in
        frappe/model/base_document.py, which runs AFTER CremaAutomationTask.validate).
        So this is enforced by frappe itself, not by our own controller: a task can no
        longer be saved on "advanced_ocr" at all, even though CremaAutomationTask.validate's
        own membership check (see test_unknown_interface_name_is_rejected) would let it
        through, since "advanced_ocr" is a real interfaces.names() member. Call
        sync_interface_options() explicitly so the Property Setter is guaranteed to
        exist regardless of test run order."""
        from crema.install import sync_interface_options

        sync_interface_options()

        doc = frappe.new_doc("Crema Automation Task")
        doc.task_name = f"_test_crema_task_{uuid.uuid4().hex[:8]}"
        doc.append("sources", {"source_type": "URL", "source_url": "https://example.invalid/source"})
        doc.schedule = "0 3 * * *"
        doc.instruction = "do something"
        doc.target_doctype = "ToDo"
        doc.interface = "advanced_ocr"
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_a_task_with_no_source_at_all_is_rejected(self):
        doc = frappe.new_doc("Crema Automation Task")
        doc.task_name = f"_test_crema_task_{uuid.uuid4().hex[:8]}"
        doc.schedule = "0 3 * * *"
        doc.instruction = "do something"
        doc.interface = "simple"
        doc.target_doctype = "ToDo"
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_a_blocked_target_doctype_is_rejected(self):
        """Convenience, not the fence — automation._validate_plan re-checks the same
        list at run time (test_automation.py) — but a save should fail immediately
        rather than only surface at the next run."""
        doc = frappe.new_doc("Crema Automation Task")
        doc.task_name = f"_test_crema_task_{uuid.uuid4().hex[:8]}"
        doc.append("sources", {"source_type": "URL", "source_url": "https://example.invalid/source"})
        doc.schedule = "0 3 * * *"
        doc.instruction = "do something"
        doc.target_doctype = "ToDo"
        with patch(
            "crema.crema.doctype.crema_automation_task.crema_automation_task.policy.blocked_doctypes",
            return_value={"ToDo"},
        ):
            with self.assertRaises(frappe.ValidationError):
                doc.insert(ignore_permissions=True)

    def test_update_source_records_needs_exactly_one_document_query(self):
        """Two would leave `allowed_names` — one flat set of record names — fencing a plan
        that can only name one doctype. A URL source alongside is fine: it is reference
        material, and the fence is still the one query's result set."""
        doc = frappe.new_doc("Crema Automation Task")
        doc.task_name = f"_test_crema_task_{uuid.uuid4().hex[:8]}"
        doc.schedule = "0 3 * * *"
        doc.instruction = "do something"
        doc.interface = "simple"
        doc.action = "Update the Records It Read"
        doc.append("sources", {"source_type": "Document Query", "source_doctype": "ToDo"})
        doc.append("sources", {"source_type": "Document Query", "source_doctype": "Note"})
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

        doc.sources.pop()
        doc.append("sources", {"source_type": "URL", "source_url": "https://example.invalid/x"})
        doc.insert(ignore_permissions=True)
        self.assertEqual(doc.target_doctype, "ToDo")  # taken from the one query source

    def test_a_url_row_clears_the_cells_that_only_a_query_uses(self):
        """Per Run, Changed and Files are grid columns now. A grid
        column's depends_on mutates the shared docfield, so it cannot hide one row's cell
        without hiding every row's — the values themselves have to be honest."""
        doc = frappe.new_doc("Crema Automation Task")
        doc.task_name = f"_test_crema_task_{uuid.uuid4().hex[:8]}"
        doc.schedule = "0 3 * * *"
        doc.instruction = "do something"
        doc.interface = "simple"
        doc.action = "No Changes"
        doc.append(
            "sources",
            {
                "source_type": "URL",
                "source_url": "https://example.invalid/x",
                "source_limit": 50,
                "incremental": 1,
                "read_attachments": 1,
            },
        )
        doc.insert(ignore_permissions=True)

        row = doc.sources[0]
        self.assertEqual(row.source_limit, 0)
        self.assertFalse(row.incremental)
        self.assertFalse(row.read_attachments)
        # A URL row carries no filter count, so What is the address alone.
        self.assertEqual(row.source_label, "example.invalid/x")

    def test_a_query_row_label_carries_its_filter_count(self):
        doc = frappe.new_doc("Crema Automation Task")
        doc.task_name = f"_test_crema_task_{uuid.uuid4().hex[:8]}"
        doc.schedule = "0 3 * * *"
        doc.instruction = "do something"
        doc.interface = "simple"
        doc.action = "No Changes"
        doc.append(
            "sources",
            {
                "source_type": "Document Query",
                "source_doctype": "ToDo",
                "source_filters": frappe.as_json([["status", "=", "Open"]]),
            },
        )
        doc.insert(ignore_permissions=True)

        self.assertEqual(doc.sources[0].source_label, "ToDo · 1 filter")

    @staticmethod
    def _task_with_source(**source) -> frappe.Document:
        """An otherwise-valid task carrying one source row, unsaved — so a test can name
        just the field it is about."""
        doc = frappe.new_doc("Crema Automation Task")
        doc.task_name = f"_test_crema_task_{uuid.uuid4().hex[:8]}"
        doc.schedule = "0 3 * * *"
        doc.instruction = "do something"
        doc.interface = "simple"
        doc.action = "No Changes"
        doc.append("sources", source)
        return doc

    def test_a_crema_doctype_is_refused_as_a_source(self):
        doc = self._task_with_source(source_type="Document Query", source_doctype="Crema Log")
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    # --- Crema Automation Source's own per-row rules -----------------------

    def test_a_document_query_without_a_record_type_is_rejected(self):
        doc = self._task_with_source(source_type="Document Query")
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_filters_that_are_not_json_are_rejected(self):
        doc = self._task_with_source(
            source_type="Document Query", source_doctype="ToDo", source_filters="{not json"
        )
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_filters_that_are_json_but_not_a_list_or_object_are_rejected(self):
        """Valid JSON, wrong shape — frappe.get_all would raise on it much later."""
        doc = self._task_with_source(
            source_type="Document Query", source_doctype="ToDo", source_filters='"a string"'
        )
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_filters_naming_a_field_that_does_not_exist_are_rejected(self):
        """The trial query is the cheapest way to reject a bad fieldname now, with
        frappe's own message, instead of at 3am in last_error."""
        doc = self._task_with_source(
            source_type="Document Query",
            source_doctype="ToDo",
            source_filters=frappe.as_json([["_not_a_real_field", "=", 1]]),
        )
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_a_blank_source_limit_takes_the_default(self):
        doc = self._task_with_source(source_type="Document Query", source_doctype="ToDo")
        doc.insert(ignore_permissions=True)
        self.assertEqual(doc.sources[0].source_limit, 50)

    def test_an_oversized_source_limit_is_clamped(self):
        doc = self._task_with_source(source_type="Document Query", source_doctype="ToDo", source_limit=500)
        doc.insert(ignore_permissions=True)
        self.assertEqual(doc.sources[0].source_limit, 200)

    # --- CremaAutomationTask's own trigger/action rules --------------------

    def test_a_document_event_trigger_without_an_event_is_rejected(self):
        doc = self._task_with_source(source_type="Document Query", source_doctype="ToDo")
        doc.trigger = "Document Event"
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_a_document_event_trigger_needs_a_document_query_source(self):
        """It runs on the record that changed, so a URL source alone cannot serve it."""
        doc = self._task_with_source(source_type="URL", source_url="https://example.invalid/source")
        doc.trigger = "Document Event"
        doc.event = "On Update"
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_a_once_trigger_without_a_run_at_is_rejected(self):
        """mandatory_depends_on says the same thing to the form, but frappe evaluates that
        client-side only — this is the fence."""
        doc = self._task_with_source(source_type="URL", source_url="https://example.invalid/source")
        doc.trigger = "Once"
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_create_or_update_records_without_a_target_doctype_is_rejected(self):
        doc = self._task_with_source(source_type="URL", source_url="https://example.invalid/source")
        doc.action = "Create or Update Records"
        doc.target_doctype = None
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_propose_only_without_a_target_doctype_is_rejected(self):
        doc = self._task_with_source(source_type="URL", source_url="https://example.invalid/source")
        doc.action = "Propose Only"
        doc.target_doctype = None
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_a_file_query_task_may_use_propose_only(self):
        """A File Query source used to allow only Create or Update Records — Propose Only
        needs the same target_doctype/match_on it does, and nothing else."""
        doc = self._task_with_source(source_type="File Query")
        doc.action = "Propose Only"
        doc.target_doctype = "Contact"
        doc.match_on = "first_name"
        doc.insert(ignore_permissions=True)  # must not raise
        self.assertEqual(doc.action, "Propose Only")

    def test_a_malformed_notify_to_address_is_rejected(self):
        doc = self._task_with_source(source_type="URL", source_url="https://example.invalid/source")
        doc.notify_to = "not-an-email"
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

    def test_several_well_formed_notify_to_addresses_are_accepted(self):
        """The negative case alone would also pass if every notify_to were rejected."""
        doc = self._task_with_source(source_type="URL", source_url="https://example.invalid/source")
        doc.notify_to = "a@example.com, b@example.com"
        doc.insert(ignore_permissions=True)  # must not raise
        self.assertTrue(doc.name)


class IntegrationTestCremaInstall(CremaFixtureTestCase):
    """install.py — after_install/hooks.after_migrate: sync_interfaces,
    sync_interface_options, sync_dashboard, sync_guardrails, sync_notification,
    sync_example_task."""

    def setUp(self) -> None:
        super().setUp()
        # Defaults are shared state on the Crema Settings Single: the real site's own
        # configuration could otherwise leak a Default Provider/Isolation User into a
        # test asserting "unconfigured" behavior.
        _clear_defaults()

    def test_after_install_creates_the_crema_user_role(self):
        from crema.install import after_install

        if frappe.db.exists("Role", "Crema User"):
            frappe.delete_doc("Role", "Crema User", ignore_permissions=True, force=True)

        after_install()

        self.assertTrue(frappe.db.exists("Role", "Crema User"))
        # desk_access=1: the role also gates the desk UI (crema.bundle.js), not just the
        # HTTP endpoints — see set_crema_user_desk_access for the existing-site patch.
        self.assertEqual(frappe.db.get_value("Role", "Crema User", "desk_access"), 1)

    def test_after_install_is_idempotent(self):
        from crema.install import after_install

        after_install()
        after_install()  # must not raise on an already-existing role

        self.assertTrue(frappe.db.exists("Role", "Crema User"))

    def test_sync_interfaces_seeds_one_row_per_interface_name_and_is_idempotent(self):
        """One row per interfaces.names() name — PREDEFINED plus every
        app-registered interface, not PREDEFINED alone: on a site where another
        installed app registers crema_interfaces, those rows are part of the
        reconciled set too."""
        from crema.install import sync_interfaces

        sync_interfaces()
        first_pass = {r.interface for r in frappe.get_single("Crema Settings").assignments}
        sync_interfaces()  # must not raise, and must not touch existing rows

        for name in interfaces.PREDEFINED:
            self.assertIn(name, first_pass, f"{name} was not seeded")
        after = {r.interface for r in frappe.get_single("Crema Settings").assignments}
        self.assertEqual(after, set(interfaces.names()))

    def test_sync_dashboard_creates_the_cards_and_charts_and_is_idempotent(self):
        from crema.install import _DASHBOARD_CARDS, _DASHBOARD_CHARTS, sync_dashboard

        sync_dashboard()
        sync_dashboard()  # must not raise on already-existing cards or charts

        for card in _DASHBOARD_CARDS:
            self.assertTrue(frappe.db.exists("Number Card", card["name"]))
        for chart in _DASHBOARD_CHARTS:
            self.assertTrue(frappe.db.exists("Dashboard Chart", chart["name"]))

    def test_workspace_blocks_point_at_cards_and_charts_that_exist(self):
        """The workspace body matches card/chart blocks to its own links and charts by
        *label* (frappe's blocks/block.js make()), and silently renders nothing on a
        miss — which is how the Setup and Activity cards went invisible once their
        Card Breaks were relabelled. Nothing here may drift again without failing."""
        import json

        workspace = frappe.get_doc("Workspace", "Crema")
        card_labels = {link.label for link in workspace.links if link.type == "Card Break"}
        chart_labels = {row.label for row in workspace.charts}
        quick_list_labels = {row.label for row in workspace.quick_lists}
        number_card_labels = {row.label for row in workspace.number_cards}

        for block in json.loads(workspace.content):
            data, kind = block.get("data", {}), block.get("type")
            if kind == "card":
                self.assertIn(data["card_name"], card_labels)
            elif kind == "chart":
                self.assertIn(data["chart_name"], chart_labels)
            elif kind == "quick_list":
                self.assertIn(data["quick_list_name"], quick_list_labels)
            elif kind == "number_card":
                self.assertIn(data["number_card_name"], number_card_labels)

    def test_sidebar_section_breaks_are_indented_with_their_children_nested(self):
        """On version-16 the sidebar is its own Workspace Sidebar fixture, so this reads
        the fixture file rather than `frappe.get_doc` (version-17 moves the same list into
        the Workspace as `sidebar_items`). Frappe's sidebar template renders a Section
        Break with `indent: 0` as a static muted caption instead of a real collapsible
        group header, and its children never get the CSS indent that nests them
        underneath — exactly the bug that left Setup/Automation/Activity looking
        disconnected from their links."""
        import json
        from pathlib import Path

        path = frappe.get_app_path("crema", "workspace_sidebar", "crema.json")
        sidebar_items = json.loads(Path(path).read_text())["items"]

        under_a_section = False
        for item in sidebar_items:
            if item["type"] == "Section Break":
                self.assertEqual(item["indent"], 1, f"{item['label']} Section Break must be indented")
                under_a_section = True
            elif item.get("child"):
                self.assertTrue(under_a_section, f"{item['label']} has no Section Break to nest under")
            else:
                under_a_section = False

    def test_workspace_blocks_fill_every_row(self):
        """Blocks flow into one continuous flex row of 12 columns, so three rules hold.

        A row must add up to 12 — a short row leaves a hole, and every later block slides
        up into it. It must also be *homogeneous*, because a block's width is a set of
        responsive classes, not a fraction: frappe's blocks/block.js gives 12..7 a plain
        col-xs-N (fixed at every width) but 6 a col-sm-6 and 4 a col-md-4, so a row of
        8 + 4 sums to 12 on a wide window and to 116% under 768px. And a row of narrower
        blocks must be closed by a spacer, so that when it does wrap on a narrow window
        the leftover space cannot pull the next section's first block into it."""
        import json

        workspace = frappe.get_doc("Workspace", "Crema")
        rows, row, width = [], [], 0
        for block in json.loads(workspace.content):
            row.append(block)
            width += block.get("data", {}).get("col", 12)
            self.assertLessEqual(width, 12, f"{block.get('id')} overflows its row")
            if width == 12:
                rows.append(row)
                row, width = [], 0
        self.assertEqual(width, 0, "the last row is not full")

        for index, row in enumerate(rows):
            cols = {b.get("data", {}).get("col", 12) for b in row}
            first = row[0].get("id")
            self.assertEqual(len(cols), 1, f"the row at {first} mixes block widths")
            if cols == {12} or index == len(rows) - 1:
                continue
            self.assertEqual(
                [b.get("type") for b in rows[index + 1]],
                ["spacer"],
                f"the row at {first} is not followed by a spacer",
            )

    # --- the one-time example task ---------------------------------------

    @staticmethod
    def _reset_example_seed() -> None:
        """frappe.db.set_default writes a tabDefaultValue row *and* a defaults-cache
        entry. The per-test savepoint rolls back the row but never the cache, so both
        have to be cleared by hand for the seed guard to be observable."""
        from crema.install import _EXAMPLE_TASK_NAME, _EXAMPLE_TASK_SEEDED

        frappe.db.set_default(_EXAMPLE_TASK_SEEDED, "")
        frappe.clear_cache()
        if frappe.db.exists("Crema Automation Task", _EXAMPLE_TASK_NAME):
            frappe.delete_doc(
                "Crema Automation Task", _EXAMPLE_TASK_NAME, ignore_permissions=True, force=True
            )

    def test_sync_example_task_seeds_one_disabled_task(self):
        from crema.install import _EXAMPLE_TASK_NAME, _EXAMPLE_TASK_SEEDED, sync_example_task

        self._reset_example_seed()
        self.addCleanup(frappe.clear_cache)

        sync_example_task()

        task = frappe.get_doc("Crema Automation Task", _EXAMPLE_TASK_NAME)
        self.assertFalse(task.enabled)  # an example that shipped enabled would write records
        self.assertEqual(task.action, "No Changes")
        # The trigger is the whole recipe now, and it forces On Update rather than
        # After Insert: inbound mail attaches the files after the insert.
        self.assertEqual(task.trigger, "Incoming Email")
        self.assertEqual(task.event, "On Update")
        self.assertEqual(len(task.sources), 1)
        self.assertEqual(task.sources[0].source_doctype, "Communication")
        self.assertTrue(task.sources[0].read_attachments)
        self.assertTrue(frappe.db.get_default(_EXAMPLE_TASK_SEEDED))

    def test_a_deleted_example_is_not_resurrected(self):
        """The guard is a one-time flag, not "does the record exist" — an admin who
        deletes the example must not get it back on the next migrate."""
        from crema.install import _EXAMPLE_TASK_NAME, sync_example_task

        self._reset_example_seed()
        self.addCleanup(frappe.clear_cache)
        sync_example_task()
        frappe.delete_doc("Crema Automation Task", _EXAMPLE_TASK_NAME, ignore_permissions=True, force=True)

        sync_example_task()

        self.assertFalse(frappe.db.exists("Crema Automation Task", _EXAMPLE_TASK_NAME))

    def test_seeded_blank_row_still_resolves_through_the_fallback_chain(self):
        """A row seeded by sync_interfaces has no provider — client._resolve must treat
        it exactly like "not configured" and keep walking interfaces.FALLBACKS."""
        from crema.install import sync_interfaces

        sync_interfaces()
        rows = frappe.get_single("Crema Settings").assignments
        row = next(r for r in rows if r.interface == "classification")
        self.assertFalse(row.provider)

        _ensure_user(TEST_ISOLATION_USER)
        _ensure_provider()
        settings = frappe.get_single("Crema Settings")
        simple_row = next(r for r in settings.assignments if r.interface == "simple")
        simple_row.provider = TEST_PROVIDER
        simple_row.model = "test-model"
        simple_row.isolation_user = TEST_ISOLATION_USER
        settings.save(ignore_permissions=True)

        cfg = client._resolve("classification")  # classification -> simple
        self.assertEqual(cfg["interface"], "simple")

    def test_sync_interface_options_stamps_the_select_options_onto_meta(self):
        """The Property Setter install.sync_interface_options writes is what the
        Crema Automation Task "AI Profile" picker actually reads at runtime — pin that
        frappe.get_meta sees it, not just that make_property_setter didn't raise.
        frappe.make_property_setter's own validate() already calls
        frappe.clear_cache(doctype=...) (see PropertySetter.validate in frappe core),
        so no explicit clear_cache call is needed here."""
        from crema.install import sync_interface_options

        sync_interface_options()

        field = frappe.get_meta("Crema Automation Task").get_field("interface")
        self.assertEqual(field.fieldtype, "Select")
        self.assertEqual(field.options.split("\n"), ["", *interfaces.selectable()])
        # advanced_ocr is INTERNAL; the other four are NOT_FOR_TASKS — each emits
        # something a task's PLAN stage cannot use, and transcribe is not even a
        # chat interface.
        for excluded in ("advanced_ocr", "view", "transform", "ocr", "transcribe"):
            self.assertNotIn(excluded, field.options.split("\n"))

    def test_ensure_isolation_user_is_idempotent(self):
        """Calling twice must return the same email and not insert a second User
        row — the exists-check at the top of _ensure_isolation_user."""
        from crema.install import _ensure_isolation_user

        first = _ensure_isolation_user()
        second = _ensure_isolation_user()

        self.assertEqual(first, second)
        self.assertEqual(frappe.db.count("User", {"email": first}), 1)

    def test_isolation_user_email_falls_back_on_a_site_name_that_is_not_an_email_domain(self):
        """A site created as `mysite` or `test_site` is not a legal email domain — no
        dot, and an underscore is not a domain character — so the plain `crema@<site>`
        form fails User.validate and takes the whole install down with it."""
        from crema.install import _isolation_user_email

        with patch.object(frappe.local, "site", "fcr.local"):
            self.assertEqual(_isolation_user_email(), "crema@fcr.local")

        cases = (("test_site", "crema@test-site.localhost"), ("mysite", "crema@mysite.localhost"))
        for site, expected in cases:
            with patch.object(frappe.local, "site", site):
                email = _isolation_user_email()
            self.assertEqual(email, expected)
            self.assertTrue(validate_email_address(email))

    def test_ensure_isolation_user_cannot_log_in(self):
        """No password is ever set, welcome-email is off, and the only role granted is
        'Crema User' — this is what makes the seeded default a safe sandbox identity
        rather than just a placeholder name."""
        from crema.install import _ensure_isolation_user
        from frappe.utils.password import get_decrypted_password

        email = _ensure_isolation_user()
        user = frappe.get_doc("User", email)

        self.assertEqual(user.send_welcome_email, 0)
        self.assertIsNone(get_decrypted_password("User", email, raise_exception=False))
        self.assertEqual({r.role for r in user.get("roles")}, {"Crema User"})

    def test_sync_interfaces_sets_default_isolation_user_when_blank(self):
        from crema.install import _ensure_isolation_user, sync_interfaces

        self.assertFalse(
            frappe.get_single("Crema Settings").default_isolation_user
        )  # setUp's _clear_defaults
        sync_interfaces()

        self.assertEqual(frappe.get_single("Crema Settings").default_isolation_user, _ensure_isolation_user())

    def test_sync_interfaces_does_not_overwrite_an_already_set_default_isolation_user(self):
        from crema.install import sync_interfaces

        _ensure_user(TEST_ISOLATION_USER)
        settings = frappe.get_single("Crema Settings")
        settings.default_isolation_user = TEST_ISOLATION_USER
        settings.save(ignore_permissions=True)
        sync_interfaces()  # must not touch an admin's own choice

        self.assertEqual(frappe.get_single("Crema Settings").default_isolation_user, TEST_ISOLATION_USER)

    def test_setting_provider_without_isolation_user_is_rejected(self):
        _ensure_provider()
        settings = frappe.get_single("Crema Settings")
        row = next(r for r in settings.assignments if r.interface == "extraction")
        row.provider = TEST_PROVIDER
        row.isolation_user = ""
        with self.assertRaises(frappe.ValidationError) as ctx:
            settings.save(ignore_permissions=True)
        self.assertIn("Isolation User", str(ctx.exception))

    def test_setting_provider_and_isolation_user_together_is_accepted(self):
        _ensure_user(TEST_ISOLATION_USER)
        _ensure_provider()
        settings = frappe.get_single("Crema Settings")
        row = next(r for r in settings.assignments if r.interface == "extraction")
        row.provider = TEST_PROVIDER
        row.model = "test-model"
        row.isolation_user = TEST_ISOLATION_USER
        settings.save(ignore_permissions=True)  # must not raise

        reloaded = next(
            r for r in frappe.get_single("Crema Settings").assignments if r.interface == "extraction"
        )
        self.assertEqual(reloaded.provider, TEST_PROVIDER)

    def test_sync_guardrails_seeds_defaults_once_and_never_resurrects_a_deleted_row(self):
        """install.sync_guardrails seeds the built-in rows, in _BUILTINS order,
        the FIRST time it runs on a site — tracked by the Single's own `seeded` flag,
        not by the row set. Deleting a row afterward and running it again must not
        bring the row back: unlike the old auto-reconcile contract this replaced, the
        guardrail table is the user's to edit (see CremaGuardrails' own docstring)."""
        from crema.install import sync_guardrails

        doc = frappe.get_single("Crema Guardrails")
        doc.guardrails = []
        doc.seeded = 0
        doc.save(ignore_permissions=True)

        sync_guardrails()
        sync_guardrails()  # already seeded — must not raise, must not duplicate rows

        seeded = frappe.get_single("Crema Guardrails")
        self.assertEqual([r.guardrail for r in seeded.guardrails], list(guardrails._BUILTINS))
        self.assertTrue(seeded.seeded)

        seeded.guardrails = [r for r in seeded.guardrails if r.guardrail != "trap"]
        seeded.save(ignore_permissions=True)
        sync_guardrails()

        reloaded = frappe.get_single("Crema Guardrails")
        self.assertNotIn("trap", {r.guardrail for r in reloaded.guardrails})

    def test_sync_notification_seeds_one_disabled_blocked_notification(self):
        """install.sync_notification — the escalation rail for Blocked rows, seeded
        DISABLED and create-if-missing only, so a migrate never re-enables or restores
        what an admin turned off or deleted after the first seed."""
        from crema.install import _BLOCKED_NOTIFICATION_SUBJECT, sync_notification

        for name in frappe.get_all(
            "Notification", filters={"subject": _BLOCKED_NOTIFICATION_SUBJECT}, pluck="name"
        ):
            frappe.delete_doc("Notification", name, ignore_permissions=True, force=True)

        sync_notification()
        sync_notification()  # create-if-missing: must not raise or duplicate

        names = frappe.get_all(
            "Notification", filters={"subject": _BLOCKED_NOTIFICATION_SUBJECT}, pluck="name"
        )
        self.assertEqual(len(names), 1)
        doc = frappe.get_doc("Notification", names[0])
        self.assertEqual(doc.document_type, "Crema Log")
        self.assertEqual(doc.condition, 'doc.status == "Blocked"')
        self.assertEqual(doc.channel, "System Notification")
        self.assertFalse(doc.enabled)
        self.assertEqual({r.receiver_by_role for r in doc.recipients}, {"System Manager"})

    def test_default_isolation_user_administrator_is_rejected(self):
        settings = frappe.get_single("Crema Settings")
        settings.default_isolation_user = "Administrator"
        with self.assertRaises(frappe.ValidationError):
            settings.save(ignore_permissions=True)

    def test_default_isolation_user_system_manager_is_rejected(self):
        sandbox_user = f"_test_crema_default_sandbox_{uuid.uuid4().hex[:8]}@example.com"
        _ensure_user(sandbox_user, roles=["System Manager"])
        settings = frappe.get_single("Crema Settings")
        settings.default_isolation_user = sandbox_user
        with self.assertRaises(frappe.ValidationError) as ctx:
            settings.save(ignore_permissions=True)
        self.assertIn("System Manager", str(ctx.exception))

    def test_default_isolation_user_disabled_is_rejected(self):
        disabled_user = f"_test_crema_default_disabled_{uuid.uuid4().hex[:8]}@example.com"
        user = frappe.new_doc("User")
        user.email = disabled_user
        user.first_name = "disabled"
        user.send_welcome_email = 0
        user.enabled = 0
        user.insert(ignore_permissions=True)

        settings = frappe.get_single("Crema Settings")
        settings.default_isolation_user = disabled_user
        with self.assertRaises(frappe.ValidationError) as ctx:
            settings.save(ignore_permissions=True)
        self.assertIn("enabled", str(ctx.exception))


class IntegrationTestCremaSettingsReconcile(CremaFixtureTestCase):
    """CremaSettings.validate's cross-row rules: the assignments child table always
    matches interfaces.PREDEFINED, in order — add a missing name, drop an unknown
    one, reorder the rest."""

    def setUp(self) -> None:
        super().setUp()
        frappe.set_user("Administrator")

    def test_save_reconciles_to_exactly_the_full_interface_name_set(self):
        """interfaces.names() — PREDEFINED plus app-registered, in that order. On a
        site where another installed app registers crema_interfaces, those rows are
        part of the fixed set, so asserting PREDEFINED alone would fail there."""
        settings = frappe.get_single("Crema Settings")
        settings.save(ignore_permissions=True)
        names = [r.interface for r in settings.assignments]
        self.assertEqual(names, interfaces.names())

    def test_a_row_with_an_interface_name_outside_the_known_set_is_dropped_on_save(self):
        settings = frappe.get_single("Crema Settings")
        settings.save(ignore_permissions=True)  # normalize first
        bogus = settings.append("assignments", {})
        bogus.interface = "_not_a_real_interface_name"
        settings.save(ignore_permissions=True)

        names = {r.interface for r in frappe.get_single("Crema Settings").assignments}
        self.assertNotIn("_not_a_real_interface_name", names)
        self.assertEqual(names, set(interfaces.names()))

    def test_reconcile_stamps_the_human_label_for_every_row(self):
        settings = frappe.get_single("Crema Settings")
        settings.save(ignore_permissions=True)

        labels = {r.interface: r.interface_label for r in settings.assignments}
        for name in interfaces.PREDEFINED:
            self.assertEqual(labels[name], interfaces.LABELS[name])

    def test_reconcile_stamps_the_app_label_when_present_else_the_raw_name(self):
        """interfaces.label_for's fallback order for an app-registered row: its own
        "label" if it supplied one, else the raw interface name — same shape as
        prompt_for/fallback_for. "label" is not a Crema Model Assignment fieldname, so
        it must not land on the row itself (unlike "prompt"/"fallback", it isn't read
        back off the row anywhere — reconcile must simply not setattr it)."""
        fake = {
            "_test_app_iface_labeled": {"prompt": "p", "label": "Nice Label"},
            "_test_app_iface_unlabeled": {"prompt": "p"},
        }
        try:
            with patch("crema.interfaces.app_interfaces", return_value=fake):
                settings = frappe.get_single("Crema Settings")
                settings.save(ignore_permissions=True)

                rows = {r.interface: r for r in settings.assignments}
                self.assertEqual(rows["_test_app_iface_labeled"].interface_label, "Nice Label")
                self.assertEqual(
                    rows["_test_app_iface_unlabeled"].interface_label, "_test_app_iface_unlabeled"
                )
                self.assertIsNone(rows["_test_app_iface_labeled"].get("label"))
        finally:
            frappe.get_single("Crema Settings").save(ignore_permissions=True)
            names = {r.interface for r in frappe.get_single("Crema Settings").assignments}
            self.assertNotIn("_test_app_iface_labeled", names)
            self.assertNotIn("_test_app_iface_unlabeled", names)

    def test_app_registered_interface_is_seeded_and_edits_survive_reconcile(self):
        """crema_interfaces (hooks.py) rows behave exactly like PREDEFINED ones: added
        when missing, seeded once from the app's config, then left alone."""
        fake = {
            "_test_app_iface": {
                "prompt": "app prompt",
                "fallback": "simple",
                "max_tokens": 512,
                "cache_ttl": 300,
            }
        }
        try:
            with patch("crema.interfaces.app_interfaces", return_value=fake):
                settings = frappe.get_single("Crema Settings")
                settings.save(ignore_permissions=True)

                reloaded = frappe.get_single("Crema Settings")
                row = next(r for r in reloaded.assignments if r.interface == "_test_app_iface")
                self.assertEqual(row.interface_label, "_test_app_iface")  # LABELS.get(name, name)
                self.assertEqual(row.system_prompt, "app prompt")
                self.assertEqual(row.max_tokens, 512)
                self.assertEqual(row.cache_ttl, 300)

                # An admin's later edit is not clobbered by a second reconcile — the
                # hook config only seeds a *new* row, once.
                row.cache_ttl = 600
                reloaded.save(ignore_permissions=True)
                row = next(
                    r
                    for r in frappe.get_single("Crema Settings").assignments
                    if r.interface == "_test_app_iface"
                )
                self.assertEqual(row.cache_ttl, 600)
        finally:
            # Outside the patch, app_interfaces() reverts to real (no app on this site
            # declares this fake name), so the next reconcile drops the row — the same
            # mechanism test_a_row_with_an_interface_name_outside_the_known_set_is_dropped_on_save
            # exercises above.
            frappe.get_single("Crema Settings").save(ignore_permissions=True)
            names = {r.interface for r in frappe.get_single("Crema Settings").assignments}
            self.assertNotIn("_test_app_iface", names)

    def test_reconcile_warns_and_drops_an_unknown_key_but_keeps_a_valid_sibling(self):
        """A crema_interfaces key that is not "prompt"/"fallback"/"label" and not a
        Crema Model Assignment field would otherwise vanish silently at insert
        (get_valid_dict drops it) — FCR-FINDINGS F1. Reconcile must warn, keep the
        row, and still seed a valid sibling key."""
        fake = {
            "_test_app_iface_bad_key": {
                "prompt": "p",
                "max_tokens": 512,
                "enable_prompt_scan": 1,  # not a Crema Model Assignment field
            }
        }
        try:
            with (
                patch("crema.interfaces.app_interfaces", return_value=fake),
                patch("frappe.logger") as mock_logger,
            ):
                settings = frappe.get_single("Crema Settings")
                settings.save(ignore_permissions=True)

            mock_logger.assert_called_once_with("crema")
            warning = mock_logger.return_value.warning.call_args[0][0]
            self.assertIn("_test_app_iface_bad_key", warning)
            self.assertIn("enable_prompt_scan", warning)

            row = next(
                r
                for r in frappe.get_single("Crema Settings").assignments
                if r.interface == "_test_app_iface_bad_key"
            )
            self.assertEqual(row.max_tokens, 512)
            self.assertIsNone(row.get("enable_prompt_scan"))
        finally:
            frappe.get_single("Crema Settings").save(ignore_permissions=True)
            names = {r.interface for r in frappe.get_single("Crema Settings").assignments}
            self.assertNotIn("_test_app_iface_bad_key", names)

    def test_row_with_provider_and_no_model_warns_on_save_but_still_saves(self):
        """A provider with no effective model resolves to a broken litellm model
        string at call time (client._complete) — CremaSettings.validate warns instead
        of blocking, since "provider picked, model not chosen yet" is a normal
        mid-setup state."""
        _ensure_user(TEST_ISOLATION_USER)
        _ensure_provider()
        # The real site this suite runs against may have a Default Model set, which
        # would legitimately silence the warning — blank the defaults first, same as
        # test_client.py's resolution tests do (undone by the class-level rollback).
        _clear_defaults()
        settings = frappe.get_single("Crema Settings")
        row = next(r for r in settings.assignments if r.interface == "extraction")
        row.provider = TEST_PROVIDER
        row.model = ""
        row.isolation_user = TEST_ISOLATION_USER
        before = len(frappe.message_log)
        settings.save(ignore_permissions=True)  # must not raise

        # The warning line reads row.interface_label ("Extraction"), not the raw key
        # ("extraction") — see test_missing_model_warning_reports_the_label_not_the_key
        # below for the assertion that pins that shape.
        new_messages = [str(m.get("message", m)) for m in frappe.message_log[before:]]
        self.assertTrue(
            any("extraction" in msg.lower() for msg in new_messages),
            new_messages,
        )

    def test_missing_model_warning_reports_the_label_not_the_key(self):
        """The warning line reads from row.interface_label — stamped by
        _reconcile_assignments before _warn_missing_models runs — not the raw
        interface key, matching every other place the desk shows an interface name."""
        _ensure_user(TEST_ISOLATION_USER)
        _ensure_provider()
        _clear_defaults()
        settings = frappe.get_single("Crema Settings")
        row = next(r for r in settings.assignments if r.interface == "extraction")
        row.provider = TEST_PROVIDER
        row.model = ""
        row.isolation_user = TEST_ISOLATION_USER
        before = len(frappe.message_log)
        settings.save(ignore_permissions=True)

        missing_model = [m for m in frappe.message_log[before:] if m.get("title") == "Missing Model"]
        self.assertTrue(missing_model, frappe.message_log[before:])
        message = str(missing_model[0]["message"])
        self.assertIn(row.interface_label, message)
        self.assertNotIn(f"'{row.interface}':", message)
        self.assertEqual(missing_model[0].get("indicator"), "orange")

    def test_create_from_template_as_default_with_no_models_emits_no_missing_model_warning(self):
        """The nested Crema Settings save inside create_from_template must not
        surface _warn_missing_models — the admin just clicked "New from Template",
        not "save settings", and a brand-new site has no model anywhere, so every
        interface would otherwise qualify as missing.

        Default Isolation User is left set (install.after_install seeds a real site's
        with a dedicated account before any provider exists) — _clear_defaults() would
        also blank it, which fails this save for an unrelated reason
        (_validate_provider_isolation_pairing) before _warn_missing_models is ever
        reached."""
        _ensure_user(TEST_ISOLATION_USER)
        settings = frappe.get_single("Crema Settings")
        settings.default_provider = ""
        settings.default_model = ""
        settings.default_isolation_user = TEST_ISOLATION_USER
        settings.flags.crema_skip_model_warning = True
        settings.save(ignore_permissions=True)

        before = len(frappe.message_log)
        name = create_from_template(
            preset="Ollama",
            provider_name=f"_test_crema_wizard_{uuid.uuid4().hex[:8]}",
            base_url=PRESETS["Ollama"],
            enabled=True,
            set_as_default=True,
        )

        new_messages = [str(m.get("message", m)) for m in frappe.message_log[before:]]
        self.assertFalse(
            any("no Model set" in msg for msg in new_messages),
            new_messages,
        )
        self.assertEqual(frappe.get_single("Crema Settings").default_provider, name)

    def test_deleting_the_default_provider_clears_default_provider_and_model(self):
        _ensure_provider()
        settings = frappe.get_single("Crema Settings")
        settings.default_provider = TEST_PROVIDER
        settings.default_model = "test-model"
        settings.flags.crema_skip_model_warning = True
        settings.save(ignore_permissions=True)

        frappe.delete_doc("Crema Provider", TEST_PROVIDER, ignore_permissions=True)

        reloaded = frappe.get_single("Crema Settings")
        self.assertEqual(reloaded.default_provider, "")
        self.assertEqual(reloaded.default_model, "")

    def test_deleting_a_provider_named_only_on_one_assignment_row_clears_that_row(self):
        """Both rows get an explicit provider — a distinct one on the survivor —
        rather than trusting whatever the site's `simple` row happens to hold: on a
        site where an earlier aborted run left `simple` pointing at the shared test
        provider, "named only on one row" would silently be false and the release
        would (correctly) clear both."""
        _ensure_provider()
        _ensure_user(TEST_ISOLATION_USER)
        _clear_defaults()
        keeper = f"_test_crema_keeper_{uuid.uuid4().hex[:8]}"
        keeper_doc = frappe.new_doc("Crema Provider")
        keeper_doc.provider_name = keeper
        keeper_doc.base_url = "http://localhost:11434/v1"
        keeper_doc.enabled = 0
        keeper_doc.insert(ignore_permissions=True)

        settings = frappe.get_single("Crema Settings")
        row = next(r for r in settings.assignments if r.interface == "extraction")
        row.provider = TEST_PROVIDER
        row.model = "test-model"
        row.isolation_user = TEST_ISOLATION_USER
        other_row = next(r for r in settings.assignments if r.interface == "simple")
        other_row.provider = keeper
        other_row.isolation_user = TEST_ISOLATION_USER
        settings.save(ignore_permissions=True)

        frappe.delete_doc("Crema Provider", TEST_PROVIDER, ignore_permissions=True)

        reloaded = frappe.get_single("Crema Settings")
        reloaded_row = next(r for r in reloaded.assignments if r.interface == "extraction")
        self.assertEqual(reloaded_row.provider, "")
        self.assertEqual(reloaded_row.model, "")
        reloaded_other = next(r for r in reloaded.assignments if r.interface == "simple")
        self.assertEqual(reloaded_other.provider, keeper)

    def test_deleting_an_unreferenced_provider_does_not_touch_settings(self):
        name = f"_test_crema_unreferenced_{uuid.uuid4().hex[:8]}"
        doc = frappe.new_doc("Crema Provider")
        doc.provider_name = name
        doc.base_url = "http://localhost:11434/v1"
        doc.enabled = 0
        doc.insert(ignore_permissions=True)

        before_modified = frappe.get_single("Crema Settings").modified
        frappe.delete_doc("Crema Provider", name, ignore_permissions=True)
        after_modified = frappe.get_single("Crema Settings").modified

        self.assertEqual(before_modified, after_modified)

    def test_settings_references_reports_default_flag_and_use_case_labels(self):
        _ensure_provider()
        settings = frappe.get_single("Crema Settings")
        settings.default_provider = TEST_PROVIDER
        row = next(r for r in settings.assignments if r.interface == "extraction")
        row.provider = TEST_PROVIDER
        settings.flags.crema_skip_model_warning = True
        settings.save(ignore_permissions=True)
        frappe.clear_document_cache("Crema Settings")

        is_default, use_cases = _settings_references(TEST_PROVIDER)

        self.assertTrue(is_default)
        self.assertIn("Extraction", use_cases)

    def test_a_provider_with_no_isolation_user_anywhere_is_rejected(self):
        """The invariant that a real provider call can never run un-sandboxed: a row
        overriding only its provider, with no row isolation_user and no Default
        Isolation User to fall back on, must not save."""
        _ensure_provider()
        _clear_defaults()
        settings = frappe.get_single("Crema Settings")
        row = next(r for r in settings.assignments if r.interface == "extraction")
        row.provider = TEST_PROVIDER
        row.isolation_user = ""

        with self.assertRaises(frappe.ValidationError):
            settings.save(ignore_permissions=True)

    def test_a_provider_with_no_row_isolation_user_is_accepted_via_the_default(self):
        """The same row as above is fine once Default Isolation User supplies the
        other half of the pairing — the rule is evaluated on effective values."""
        _ensure_user(TEST_ISOLATION_USER)
        _ensure_provider()
        settings = frappe.get_single("Crema Settings")
        settings.default_isolation_user = TEST_ISOLATION_USER
        row = next(r for r in settings.assignments if r.interface == "extraction")
        row.provider = TEST_PROVIDER
        row.isolation_user = ""

        settings.save(ignore_permissions=True)  # must not raise

        reloaded = next(
            r for r in frappe.get_single("Crema Settings").assignments if r.interface == "extraction"
        )
        self.assertEqual(reloaded.provider, TEST_PROVIDER)


class IntegrationTestCremaGuardrails(CremaFixtureTestCase):
    """CremaGuardrails.validate — _drop_orphans (the one row Frappe's own Select
    validation would otherwise brick the Single over), the use-case filter fence
    (_validate_filters), and the _warn_audio msgprint."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.ensure_fixtures("simple")

    def test_saving_an_emptied_table_leaves_it_empty(self):
        """Unlike the old auto-reconcile, deleting every row and saving keeps the
        table empty — nothing here re-seeds it. install.sync_guardrails is the only
        thing that seeds the built-ins, and only the first time it ever runs on a
        site (IntegrationTestCremaInstall)."""
        doc = frappe.get_single("Crema Guardrails")
        doc.guardrails = []
        doc.save(ignore_permissions=True)  # must not raise

        self.assertEqual(frappe.get_single("Crema Guardrails").guardrails, [])

    def test_a_reordered_row_list_survives_the_next_save(self):
        """Row order is execution order here, so _reconcile is order-preserving —
        unlike CremaSettings._reconcile_assignments, which re-sorts to a fixed order.
        An admin's drag-reorder must come back from the save intact."""
        doc = frappe.get_single("Crema Guardrails")
        doc.guardrails = list(reversed(doc.guardrails))
        for idx, row in enumerate(doc.guardrails, start=1):
            row.idx = idx
        reordered = [r.guardrail for r in doc.guardrails]
        doc.save(ignore_permissions=True)

        rows = frappe.get_single("Crema Guardrails").guardrails
        self.assertEqual([r.guardrail for r in rows], reordered)

    def test_a_row_with_an_unknown_guardrail_key_is_dropped_on_save(self):
        doc = frappe.get_single("Crema Guardrails")
        bogus = doc.append("guardrails", {})
        bogus.guardrail = "_not_a_real_guardrail"
        doc.save(ignore_permissions=True)

        keys = {r.guardrail for r in frappe.get_single("Crema Guardrails").guardrails}
        self.assertNotIn("_not_a_real_guardrail", keys)
        self.assertEqual(keys, set(guardrails.registry()))

    def test_an_unknown_use_case_token_in_the_filter_is_rejected(self):
        with self.assertRaises(frappe.ValidationError) as ctx:
            _set_guardrail("scan", "Block", interfaces_filter="simple, _not_a_real_use_case")
        self.assertIn("_not_a_real_use_case", str(ctx.exception))

    def test_a_valid_use_case_filter_is_normalized_to_comma_space(self):
        _set_guardrail("scan", "Block", interfaces_filter="simple,extraction,  translation")

        row = next(r for r in frappe.get_single("Crema Guardrails").guardrails if r.guardrail == "scan")
        self.assertEqual(row.interfaces, "simple, extraction, translation")

    def test_a_non_off_row_naming_transcribe_in_its_filter_warns_on_save(self):
        """_warn_audio — audio has no text to scan or mask, so a filter that names the
        transcribe use case msgprints (never throws); a blank everywhere-filter stays
        silent, or every save would nag."""
        before = len(frappe.message_log)
        _set_guardrail("scan", "Block", interfaces_filter="transcribe")  # must not raise

        warnings = [m for m in frappe.message_log[before:] if m.get("title") == "Guardrails and audio"]
        self.assertTrue(warnings, frappe.message_log[before:])
        self.assertEqual(warnings[0].get("indicator"), "orange")
        self.assertIn("Text Scan", str(warnings[0]["message"]))

    def test_a_blank_filter_does_not_trigger_the_audio_warning(self):
        """The other half of _warn_audio's fence: blank means "everywhere", including
        transcribe, and that must not warn on every routine save."""
        before = len(frappe.message_log)
        _set_guardrail("scan", "Block", interfaces_filter="")

        warnings = [m for m in frappe.message_log[before:] if m.get("title") == "Guardrails and audio"]
        self.assertEqual(warnings, [])


class IntegrationTestCremaConfigure(CremaFixtureTestCase):
    """crema.api.configure() — the supported code-side setter for one interface's
    model/provider/monthly_budget_usd (PLAN.md item 9)."""

    def setUp(self) -> None:
        super().setUp()
        frappe.set_user("Administrator")

    def test_unknown_interface_name_raises(self):
        with self.assertRaises(CremaConfigError):
            api.configure("_not_a_real_interface_name", model="whatever")

    def test_configure_creates_a_missing_row_and_sets_the_model(self):
        """A brand-new app-registered name that has never been saved has no row yet —
        configure() must create it via the same reconcile a settings.save() runs, not
        fail looking one up. Asserted against the row itself, not the returned
        health() dict: whether it resolves as "configured" depends on whether this
        site's own Default Provider/Model are already set, which this test must not
        assume either way."""
        fake = {"_test_app_iface_configure": {"prompt": "p"}}
        try:
            with patch("crema.interfaces.app_interfaces", return_value=fake):
                api.configure("_test_app_iface_configure", model="my-model")

                settings = frappe.get_single("Crema Settings")
                row = next(r for r in settings.assignments if r.interface == "_test_app_iface_configure")
                self.assertEqual(row.model, "my-model")
        finally:
            frappe.get_single("Crema Settings").save(ignore_permissions=True)
            names = {r.interface for r in frappe.get_single("Crema Settings").assignments}
            self.assertNotIn("_test_app_iface_configure", names)

    def test_a_field_left_out_is_untouched_and_a_second_call_is_idempotent(self):
        _ensure_user(TEST_ISOLATION_USER)
        _ensure_provider()
        settings = frappe.get_single("Crema Settings")
        row = next(r for r in settings.assignments if r.interface == "translation")
        row.provider = TEST_PROVIDER
        row.isolation_user = TEST_ISOLATION_USER
        settings.save(ignore_permissions=True)

        result = api.configure("translation", model="second-model")

        self.assertEqual(result["model"], "second-model")
        reloaded = frappe.get_single("Crema Settings")
        row = next(r for r in reloaded.assignments if r.interface == "translation")
        self.assertEqual(row.model, "second-model")
        self.assertEqual(row.provider, TEST_PROVIDER)  # untouched, not cleared

        api.configure("translation", model="second-model")  # idempotent
        row = next(r for r in frappe.get_single("Crema Settings").assignments if r.interface == "translation")
        self.assertEqual(row.model, "second-model")


class IntegrationTestCremaTesting(CremaFixtureTestCase):
    """crema.testing.seed_provider — the supported bootstrap a consuming app's own
    before_tests calls instead of hand-building a Crema Provider row (PLAN.md item 8).
    No explicit cleanup beyond the per-test savepoint: seed_provider never commits."""

    def test_seed_provider_is_idempotent(self):
        name = f"_test_seed_provider_{uuid.uuid4().hex[:8]}"
        testing.seed_provider(name, set_defaults=False)
        testing.seed_provider(name, set_defaults=False)  # must not raise or duplicate

        self.assertEqual(frappe.db.count("Crema Provider", {"provider_name": name}), 1)

    def test_seed_provider_does_not_clobber_an_already_set_default(self):
        _ensure_user(TEST_ISOLATION_USER)
        _ensure_provider()
        settings = frappe.get_single("Crema Settings")
        settings.default_provider = TEST_PROVIDER
        settings.default_isolation_user = TEST_ISOLATION_USER
        settings.default_model = "already-set-model"
        settings.save(ignore_permissions=True)

        name = f"_test_seed_provider_default_{uuid.uuid4().hex[:8]}"
        testing.seed_provider(name)

        reloaded = frappe.get_single("Crema Settings")
        self.assertEqual(reloaded.default_provider, TEST_PROVIDER)
        self.assertEqual(reloaded.default_model, "already-set-model")


class IntegrationTestCremaLogInsert(IntegrationTestCase):
    """crema.log.insert — writes the audit row and degrades quietly on its own failure."""

    def setUp(self) -> None:
        super().setUp()
        frappe.set_user("Administrator")

    def test_insert_failure_degrades_to_log_error_without_raising(self):
        with patch("frappe.get_doc", side_effect=Exception("db is on fire")):
            with patch("frappe.log_error") as mock_log_error:
                log.insert("simple", "test-model", "Success", None)  # must not raise

        mock_log_error.assert_called_once()

    def test_insert_writes_provider(self):
        log.insert("simple", "test-model", "Success", None, provider="_Test Crema Provider")
        row = frappe.get_last_doc("Crema Log", filters={"interface": "simple", "status": "Success"})
        self.assertEqual(row.provider, "_Test Crema Provider")

    def test_insert_stamps_the_crema_app_version(self):
        import crema

        log.insert("simple", "test-model", "Success", None, prompt_sha="app-version-probe")
        row = frappe.get_doc("Crema Log", {"prompt_sha": "app-version-probe"})
        self.assertEqual(row.app_version, crema.__version__)


class IntegrationTestCremaLogMeta(IntegrationTestCase):
    """Crema Log list/report metadata — title, columns, and app-registered interface
    labels via interfaces.label_for()."""

    def test_list_shows_the_labelled_use_case_as_title_with_tokens_and_cost_beside_it(self):
        """The subject column is the label ("Default"), never the raw key ("simple") —
        the key stays stored, since month_spend/check_budget and Crema Usage group on it.

        status is deliberately absent: crema_log_list.js renders it as the indicator pill,
        and a column would print the same word again one cell to the right."""
        meta = frappe.get_meta("Crema Log")
        self.assertEqual(meta.title_field, "interface_label")
        self.assertEqual(
            {df.fieldname for df in meta.fields if df.in_list_view},
            {"user", "total_tokens", "cost_usd"},
        )
        self.assertTrue(meta.get_field("status").in_standard_filter)
        self.assertTrue(meta.get_field("section_diag").collapsible)

    def test_insert_stamps_the_label_beside_the_raw_interface_key(self):
        log.insert("simple", "test-model", "Success", None, prompt_sha="label-stamp-probe")
        row = frappe.get_doc("Crema Log", {"prompt_sha": "label-stamp-probe"})
        self.assertEqual(row.interface, "simple")
        self.assertEqual(row.interface_label, interfaces.LABELS["simple"])

    def test_insert_stamps_an_app_interfaces_own_label(self):
        """A row logged under an app-registered interface carries the app's own label
        (interfaces.label_for), not the raw key — same as the Crema Settings grid."""
        fake = {"_test_app_iface_logged": {"prompt": "p", "label": "App Label"}}
        with patch("crema.interfaces.app_interfaces", return_value=fake):
            log.insert("_test_app_iface_logged", "test-model", "Success", None, prompt_sha="app-label-probe")
        row = frappe.get_doc("Crema Log", {"prompt_sha": "app-label-probe"})
        self.assertEqual(row.interface_label, "App Label")

    def test_no_field_can_hold_prompt_or_document_text(self):
        """The layout change moved fields around. It must not have added one that could
        hold prompt, context, or document text — see log.insert and docs/security.md.

        The developer_mode transcript deliberately does not weaken this: it is a Comment
        on the row, not a field of it."""
        free_text = {"Small Text", "Text", "Long Text", "Text Editor", "Code", "HTML"}
        meta = frappe.get_meta("Crema Log")
        self.assertEqual(
            {df.fieldname for df in meta.fields if df.fieldtype in free_text},
            {"detail"},
        )


class IntegrationTestCremaLogChain(CremaFixtureTestCase):
    """CremaLog.before_insert's hash chain and crema.log.verify_chain — tamper-evidence
    for the audit log (PLAN.md, Done: tamper-evident log chain).

    Subclasses CremaFixtureTestCase for its per-test savepoint alone (no
    _ensure_user/_ensure_provider/_ensure_interface call here): verify_chain walks the
    whole table, so one test method's rows surviving into the next — plain
    IntegrationTestCase's only rollback is per-class, not per-method — would corrupt
    every later method's own "clean chain" precondition.
    """

    def setUp(self) -> None:
        super().setUp()
        # The site's own Crema Log rows (a desk call, a cypress run) sit before this
        # test's rows in the whole-table walk and break its clean-chain premise.
        # The delete runs inside the per-test savepoint, so the rows come back.
        frappe.db.delete("Crema Log")

    @staticmethod
    def _insert(prompt_sha: str) -> str:
        log.insert("simple", "test-model", "Success", None, prompt_sha=prompt_sha)
        return frappe.get_doc("Crema Log", {"prompt_sha": prompt_sha}).name

    def test_freshly_inserted_rows_chain_together_and_verify_clean(self):
        tag = uuid.uuid4().hex
        first = self._insert(f"chain-clean-a-{tag}")
        second = self._insert(f"chain-clean-b-{tag}")
        third = self._insert(f"chain-clean-c-{tag}")

        rows = [frappe.db.get_value("Crema Log", n, "chain_sha") for n in (first, second, third)]
        self.assertTrue(all(rows), "every fresh row must carry a chain_sha")
        self.assertEqual(len(set(rows)), 3, "each row's chain_sha must differ from the others")

        result = log.verify_chain()
        self.assertTrue(result["ok"])
        self.assertIsNone(result["first_break"])

    def test_editing_a_row_field_makes_verify_chain_report_that_row(self):
        tag = uuid.uuid4().hex
        self._insert(f"chain-edit-a-{tag}")
        middle = self._insert(f"chain-edit-b-{tag}")
        self._insert(f"chain-edit-c-{tag}")
        self.assertTrue(log.verify_chain()["ok"], "must be clean before the tamper")

        frappe.db.set_value("Crema Log", middle, "detail", "tampered after the fact")

        result = log.verify_chain()
        self.assertFalse(result["ok"])
        self.assertEqual(result["first_break"], middle)

    def test_editing_served_model_makes_verify_chain_report_that_row(self):
        """served_model is chained (CHAIN_FIELDS) precisely so an incident's audit trail
        can't be quietly repointed at a different model after the fact."""
        tag = uuid.uuid4().hex
        self._insert(f"chain-served-a-{tag}")
        middle = self._insert(f"chain-served-b-{tag}")
        self._insert(f"chain-served-c-{tag}")
        self.assertTrue(log.verify_chain()["ok"], "must be clean before the tamper")

        frappe.db.set_value("Crema Log", middle, "served_model", "a-different-model")

        result = log.verify_chain()
        self.assertFalse(result["ok"])
        self.assertEqual(result["first_break"], middle)

    def test_deleting_a_mid_chain_row_makes_verify_chain_report_its_successor(self):
        """A mid-chain deletion, not the oldest row — deleting the oldest is the one
        deletion verify_chain deliberately cannot see (the next test)."""
        tag = uuid.uuid4().hex
        self._insert(f"chain-del-a-{tag}")
        middle = self._insert(f"chain-del-b-{tag}")
        third = self._insert(f"chain-del-c-{tag}")
        self.assertTrue(log.verify_chain()["ok"], "must be clean before the deletion")

        frappe.db.delete("Crema Log", {"name": middle})

        result = log.verify_chain()
        self.assertFalse(result["ok"])
        self.assertEqual(result["first_break"], third)

    def test_deleting_the_oldest_row_keeps_the_chain_clean(self):
        """automation.cleanup_logs deletes the oldest rows daily, so verify_chain
        anchors on the oldest survivor's stored chain_sha (docs/security.md: a break
        before it is undetectable by construction) — routine retention must never
        read as tampering."""
        tag = uuid.uuid4().hex
        first = self._insert(f"chain-old-a-{tag}")
        self._insert(f"chain-old-b-{tag}")
        self._insert(f"chain-old-c-{tag}")
        self.assertTrue(log.verify_chain()["ok"], "must be clean before the deletion")

        frappe.db.delete("Crema Log", {"name": first})

        result = log.verify_chain()
        self.assertTrue(result["ok"])
        self.assertIsNone(result["first_break"])

    def test_a_row_with_real_usage_numbers_survives_the_database_round_trip(self):
        """before_insert hashes python values; verify_chain re-reads the database's own:
        cost_usd goes through a decimal(21,9) column, and client._record_usage's float
        accumulation routinely carries artifacts past 9 decimals (0.1 + 0.2). chain_hash
        canonicalises both views to one string — without that, every row with a real
        cost reads as tampered. The plain row first matters: the table's oldest row is
        the trusted anchor, so only a row after it is actually verified."""
        self._insert(f"chain-cost-anchor-{uuid.uuid4().hex}")
        frappe.get_doc(
            {
                "doctype": "Crema Log",
                "interface": "simple",
                "model": "test-model",
                "user": frappe.session.user,
                "status": "Success",
                "prompt_sha": f"chain-cost-{uuid.uuid4().hex}",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "llm_calls": 2,
                "cost_usd": 0.1 + 0.2,  # 0.30000000000000004 — the classic float artifact
            }
        ).insert(ignore_permissions=True)

        result = log.verify_chain()
        self.assertTrue(result["ok"])
        self.assertIsNone(result["first_break"])


class IntegrationTestCremaPolicy(CremaFixtureTestCase):
    """crema.policy against a real Crema Settings row. Every other blocked-doctype test
    (here and in test_automation.py) patches policy.blocked_doctypes to test its own
    gate in isolation — so this is the one place a typo in the child table's fieldname,
    its document_type column, or extend_bootinfo's boot key would actually fail.
    CremaFixtureTestCase's own tearDown already rolls the settings edit back and clears
    the get_cached_doc copy blocked_doctypes() reads."""

    def test_blocked_doctypes_reads_the_settings_table_and_extend_bootinfo_publishes_it(self):
        settings = frappe.get_doc("Crema Settings")
        settings.append("blocked_doctypes", {"document_type": "ToDo"})
        settings.save()

        self.assertIn("ToDo", policy.blocked_doctypes())

        bootinfo = frappe._dict()
        policy.extend_bootinfo(bootinfo)
        self.assertIn("ToDo", bootinfo.crema_blocked_doctypes)


class IntegrationTestCremaListColumns(IntegrationTestCase):
    """A field the list's get_indicator already renders, or that is the autoname source,
    must not also be a column — that is the same value twice on one row. See the list-view
    convention in CLAUDE.md, and the *_list.js files these mirror."""

    def test_automation_task_list_does_not_column_what_the_indicator_reads(self):
        meta = frappe.get_meta("Crema Automation Task")
        # title_field, so the subject column header reads "Task Name" and not "ID";
        # crema_automation_task_list.js sets hide_name_column to stop frappe appending
        # its own ID column beside it.
        self.assertEqual(meta.title_field, "task_name")
        self.assertEqual(
            {df.fieldname for df in meta.fields if df.in_list_view},
            {"last_run", "trigger", "interface"},
        )

    def test_provider_list_does_not_column_what_the_indicator_reads(self):
        meta = frappe.get_meta("Crema Provider")
        self.assertEqual(meta.title_field, "provider_name")
        self.assertEqual(
            {df.fieldname for df in meta.fields if df.in_list_view},
            {"base_url", "enabled"},
        )


class IntegrationTestCremaLogTranscript(IntegrationTestCase):
    """crema.client._record_transcript + crema.log._attach_transcript — the developer-mode
    only path that puts the LLM round trip in the Crema Log row's timeline."""

    def setUp(self):
        super().setUp()
        frappe.local.crema_transcript = None
        self.addCleanup(lambda: setattr(frappe.local, "crema_transcript", None))

    @staticmethod
    def _comments(prompt_sha: str) -> list[str]:
        row = frappe.get_doc("Crema Log", {"prompt_sha": prompt_sha})
        return frappe.get_all(
            "Comment",
            filters={"reference_doctype": "Crema Log", "reference_name": row.name},
            pluck="content",
        )

    def test_nothing_is_recorded_when_the_site_is_not_in_developer_mode(self):
        with patch.dict(frappe.conf, {"developer_mode": 0}):
            client._record_transcript([{"role": "user", "content": "hello"}], "hi")
        log.insert("simple", "test-model", "Success", None, prompt_sha="transcript-off")

        self.assertEqual(self._comments("transcript-off"), [])

    def test_developer_mode_attaches_every_round_trip_with_api_keys_redacted(self):
        # Reset to {}, never None: client._api_key treats the attribute as present once
        # it exists and does `provider not in frappe.local.crema_keys`, which a None
        # would turn into a TypeError for every later test in the run.
        frappe.local.crema_keys = {"_Test Crema Provider": "sk-secret-key"}
        self.addCleanup(lambda: setattr(frappe.local, "crema_keys", {}))

        with patch.dict(frappe.conf, {"developer_mode": 1}):
            client._record_transcript([{"role": "user", "content": "first sk-secret-key"}], "one")
            client._record_transcript([{"role": "user", "content": "second"}], "two")
        log.insert("simple", "test-model", "Success", None, prompt_sha="transcript-on")

        comments = self._comments("transcript-on")
        self.assertEqual(len(comments), 1)
        self.assertIn("one", comments[0])
        self.assertIn("two", comments[0])
        self.assertIn("Call 2", comments[0])
        self.assertNotIn("sk-secret-key", comments[0])

    def test_the_accumulator_is_drained_so_it_never_trails_onto_the_next_row(self):
        with patch.dict(frappe.conf, {"developer_mode": 1}):
            client._record_transcript([{"role": "user", "content": "only once"}], "once")
        log.insert("simple", "test-model", "Success", None, prompt_sha="transcript-first")
        log.insert("simple", "test-model", "Success", None, prompt_sha="transcript-second")

        self.assertEqual(len(self._comments("transcript-first")), 1)
        self.assertEqual(self._comments("transcript-second"), [])


class IntegrationTestCremaSeededIsolationUser(IntegrationTestCase):
    """The seeded isolation user's display name — install.py's _isolation_user_email."""

    def test_the_seeded_isolation_user_always_has_a_name(self):
        """A Link to User renders the document name — the email — because core User does
        not set show_title_field_in_link, and there is no per-field override. So "Runs As"
        showing an email is stock Frappe, not a missing name. This pins the name anyway,
        so a fresh install can never produce the nameless user that would look identical."""
        from crema.install import _ensure_isolation_user

        user = frappe.get_doc("User", _ensure_isolation_user())
        self.assertTrue(user.first_name)
        self.assertTrue(user.full_name)
