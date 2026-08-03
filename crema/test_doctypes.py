"""Integration tests for the crema doctype controllers not already covered by
test_client.py's IntegrationTestCremaModelAssignmentValidation, plus install.py's
seeding, crema_provider.get_presets, and crema.log.insert's failure-degrades-quietly
path.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import frappe
from crema import cache, client, interfaces, log
from crema.crema.doctype.crema_provider.crema_provider import PRESETS, _is_local_or_private, get_presets
from crema.test_fixtures import (
    TEST_ISOLATION_USER,
    TEST_PLAIN_USER,
    TEST_PROVIDER,
    CremaFixtureTestCase,
    _clear_defaults,
    _ensure_provider,
    _ensure_user,
)
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import validate_email_address


class UnitTestIsLocalOrPrivate(UnitTestCase):
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


class IntegrationTestCremaProvider(CremaFixtureTestCase):
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

    def test_get_presets_rejects_non_system_manager(self):
        _ensure_user(TEST_PLAIN_USER)
        frappe.set_user(TEST_PLAIN_USER)
        with self.assertRaises(frappe.PermissionError):
            get_presets()

    def test_create_from_template_creates_and_sets_default(self):
        from crema.crema.doctype.crema_provider.crema_provider import create_from_template

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


class IntegrationTestCremaAutomationTaskValidation(IntegrationTestCase):
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
        longer be saved on "security" at all, even though CremaAutomationTask.validate's
        own membership check (see test_unknown_interface_name_is_rejected) would let it
        through, since "security" is a real interfaces.names() member. Call
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
        doc.interface = "security"
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
        """Records Per Run, Only Changed and Attachments are grid columns now. A grid
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
        self.assertEqual(row.source_note, "")

    def test_a_query_row_summarises_only_its_filters(self):
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

        self.assertEqual(doc.sources[0].source_note, "1 filter")

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

    def test_create_or_update_records_without_a_target_doctype_is_rejected(self):
        doc = self._task_with_source(source_type="URL", source_url="https://example.invalid/source")
        doc.action = "Create or Update Records"
        doc.target_doctype = None
        with self.assertRaises(frappe.ValidationError):
            doc.insert(ignore_permissions=True)

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

    def test_sync_dashboard_creates_the_three_number_cards_and_is_idempotent(self):
        from crema.install import _DASHBOARD_CARDS, sync_dashboard

        sync_dashboard()
        sync_dashboard()  # must not raise on already-existing cards

        for card in _DASHBOARD_CARDS:
            self.assertTrue(frappe.db.exists("Number Card", card["name"]))

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
        # On Update, not After Insert: inbound mail attaches the files after the insert.
        self.assertEqual(task.trigger, "Document Event")
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
        Crema Automation Task "Use Case" picker actually reads at runtime — pin that
        frappe.get_meta sees it, not just that make_property_setter didn't raise.
        frappe.make_property_setter's own validate() already calls
        frappe.clear_cache(doctype=...) (see PropertySetter.validate in frappe core),
        so no explicit clear_cache call is needed here."""
        from crema.install import sync_interface_options

        sync_interface_options()

        field = frappe.get_meta("Crema Automation Task").get_field("interface")
        self.assertEqual(field.fieldtype, "Select")
        self.assertEqual(field.options.split("\n"), ["", *interfaces.selectable()])
        self.assertNotIn("security", field.options.split("\n"))
        self.assertNotIn("advanced_ocr", field.options.split("\n"))

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

    def test_enabling_llm_guard_is_rejected_while_seeded_security_row_has_no_provider(self):
        """Distinct from the equivalent test in test_client.py, which configures
        "security" with a real provider outright — this pins the more common real
        case: the row exists (sync_interfaces seeds it) but is not yet configured."""
        settings = frappe.get_single("Crema Settings")
        security_row = next(r for r in settings.assignments if r.interface == "security")
        security_row.provider = ""
        settings.save(ignore_permissions=True)

        _ensure_user(TEST_ISOLATION_USER)
        _ensure_provider()
        settings = frappe.get_single("Crema Settings")
        row = next(r for r in settings.assignments if r.interface == "translation")
        row.provider = TEST_PROVIDER
        row.model = "test-model"
        row.isolation_user = TEST_ISOLATION_USER
        row.enable_llm_guard = 1
        with self.assertRaises(frappe.ValidationError) as ctx:
            settings.save(ignore_permissions=True)
        self.assertIn("security", str(ctx.exception))

    def test_enabling_llm_guard_is_permitted_when_only_default_provider_is_configured(self):
        """ "security" itself can stay unconfigured at the row level as long as Crema
        Settings' Default Provider is set — client._load_from_db resolves "security"
        through the default like any other interface (CremaSettings._apply_security_
        guard_rules treats a default_provider as "security configured")."""
        _ensure_user(TEST_ISOLATION_USER)
        _ensure_provider()
        settings = frappe.get_single("Crema Settings")
        security_row = next(r for r in settings.assignments if r.interface == "security")
        security_row.provider = ""
        settings.default_provider = TEST_PROVIDER
        settings.default_isolation_user = TEST_ISOLATION_USER
        row = next(r for r in settings.assignments if r.interface == "translation")
        row.provider = TEST_PROVIDER
        row.model = "test-model"
        row.isolation_user = TEST_ISOLATION_USER
        row.enable_llm_guard = 1
        settings.save(ignore_permissions=True)  # must not raise

        reloaded = next(
            r for r in frappe.get_single("Crema Settings").assignments if r.interface == "translation"
        )
        self.assertEqual(reloaded.enable_llm_guard, 1)

    def test_enabling_llm_guard_is_rejected_when_security_provider_is_disabled(self):
        """_apply_security_guard_rules now requires the effective security provider to
        be *enabled*, not just named — a disabled provider can never actually run the
        guard call, so it must be treated the same as "not configured"."""
        disabled_provider = f"_test_crema_disabled_provider_{uuid.uuid4().hex[:8]}"
        doc = frappe.new_doc("Crema Provider")
        doc.provider_name = disabled_provider
        doc.base_url = "http://localhost:11434/v1"
        doc.enabled = 0
        doc.insert(ignore_permissions=True)
        try:
            _ensure_user(TEST_ISOLATION_USER)
            _ensure_provider()
            settings = frappe.get_single("Crema Settings")
            security_row = next(r for r in settings.assignments if r.interface == "security")
            security_row.provider = disabled_provider
            security_row.isolation_user = TEST_ISOLATION_USER
            row = next(r for r in settings.assignments if r.interface == "translation")
            row.provider = TEST_PROVIDER
            row.model = "test-model"
            row.isolation_user = TEST_ISOLATION_USER
            row.enable_llm_guard = 1
            with self.assertRaises(frappe.ValidationError) as ctx:
                settings.save(ignore_permissions=True)
            self.assertIn("security", str(ctx.exception))
        finally:
            frappe.delete_doc("Crema Provider", disabled_provider, ignore_permissions=True, force=True)

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

    def test_security_row_cannot_enable_its_own_llm_guard(self):
        settings = frappe.get_single("Crema Settings")
        row = next(r for r in settings.assignments if r.interface == "security")
        row.enable_llm_guard = 1
        settings.save(ignore_permissions=True)

        rows = frappe.get_single("Crema Settings").assignments
        reloaded = next(r for r in rows if r.interface == "security")
        self.assertEqual(reloaded.enable_llm_guard, 0)

    def test_reconcile_stamps_the_human_label_for_every_row(self):
        settings = frappe.get_single("Crema Settings")
        settings.save(ignore_permissions=True)

        labels = {r.interface: r.interface_label for r in settings.assignments}
        for name in interfaces.PREDEFINED:
            self.assertEqual(labels[name], interfaces.LABELS[name])

    def test_app_registered_interface_is_seeded_and_edits_survive_reconcile(self):
        """crema_interfaces (hooks.py) rows behave exactly like PREDEFINED ones: added
        when missing, seeded once from the app's config, then left alone."""
        fake = {
            "_test_app_iface": {
                "prompt": "app prompt",
                "fallback": "simple",
                "enable_prompt_scan": 0,
                "output_trap": "Log Only",
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
                self.assertEqual(row.enable_prompt_scan, 0)
                self.assertEqual(row.output_trap, "Log Only")

                # An admin's later edit is not clobbered by a second reconcile — the
                # hook config only seeds a *new* row, once.
                row.output_trap = "Block"
                reloaded.save(ignore_permissions=True)
                row = next(
                    r
                    for r in frappe.get_single("Crema Settings").assignments
                    if r.interface == "_test_app_iface"
                )
                self.assertEqual(row.output_trap, "Block")
        finally:
            # Outside the patch, app_interfaces() reverts to real (no app on this site
            # declares this fake name), so the next reconcile drops the row — the same
            # mechanism test_a_row_with_an_interface_name_outside_the_known_set_is_dropped_on_save
            # exercises above.
            frappe.get_single("Crema Settings").save(ignore_permissions=True)
            names = {r.interface for r in frappe.get_single("Crema Settings").assignments}
            self.assertNotIn("_test_app_iface", names)

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

        new_messages = [str(m.get("message", m)) for m in frappe.message_log[before:]]
        self.assertTrue(
            any("extraction" in msg for msg in new_messages),
            new_messages,
        )


class IntegrationTestCremaLogInsert(IntegrationTestCase):
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


class IntegrationTestCremaLogMeta(IntegrationTestCase):
    def test_list_shows_the_interface_as_title_with_cost_beside_it(self):
        meta = frappe.get_meta("Crema Log")
        self.assertEqual(meta.title_field, "interface")
        self.assertEqual(
            {df.fieldname for df in meta.fields if df.in_list_view},
            {"user", "status", "cost_usd"},
        )
        self.assertTrue(meta.get_field("section_diag").collapsible)

    def test_no_field_can_hold_prompt_or_document_text(self):
        """The layout change moved fields around. It must not have added one that could
        hold prompt, context, or document text — see log.insert and docs/security.md."""
        free_text = {"Small Text", "Text", "Long Text", "Text Editor", "Code", "HTML"}
        meta = frappe.get_meta("Crema Log")
        self.assertEqual(
            {df.fieldname for df in meta.fields if df.fieldtype in free_text},
            {"detail"},
        )
