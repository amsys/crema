"""Tests for crema/patches/consolidate_guardrails.py — the one-shot fold of the four
orphaned per-interface safety columns into Crema Guardrails rows.

A test site has no orphaned columns (they only exist on a site upgrading from the
per-interface era), so these tests add them to `tabCrema Model Assignment` by hand.
MariaDB DDL commits implicitly, which breaks savepoint-based cleanup — this class
therefore restores everything explicitly in tearDown instead of subclassing
CremaFixtureTestCase.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import frappe
from crema import log
from crema.patches import consolidate_guardrails, rechain_crema_log_for_version_fields
from crema.test_fixtures import _GUARDRAIL_BASELINE, CremaFixtureTestCase
from frappe.tests import IntegrationTestCase, UnitTestCase

_COLUMNS = {
    "enable_prompt_scan": "int(1) not null default 0",
    "output_trap": "varchar(140)",
    "masking": "varchar(140)",
}


def _clear_column_cache() -> None:
    """frappe.db.has_column reads a client-cached column list that raw DDL does not
    invalidate — without this, execute()'s own has_column check reads the stale list
    and the patch early-returns as if the columns were never added."""
    frappe.client_cache.delete_value("table_columns::tabCrema Model Assignment")


class UnitTestCremaMostSevere(UnitTestCase):
    """consolidate_guardrails._most_severe — the Off < Log Only < Retry Once < Block
    fold shared with guardrails.SEVERITY."""

    def test_most_severe_picks_the_highest_ladder_value(self):
        self.assertEqual(consolidate_guardrails._most_severe(["Off", "Block", "Log Only"]), "Block")
        self.assertEqual(consolidate_guardrails._most_severe(["Log Only", "Retry Once"]), "Retry Once")
        self.assertEqual(consolidate_guardrails._most_severe(["Off"]), "Off")

    def test_most_severe_of_an_empty_list_is_off(self):
        self.assertEqual(consolidate_guardrails._most_severe([]), "Off")


class IntegrationTestCremaConsolidateGuardrails(IntegrationTestCase):
    """consolidate_guardrails.execute — checkbox folding, ladder folding with
    most-severe-wins, the Use Cases filter union, and the fresh-install early
    return."""

    def setUp(self) -> None:
        super().setUp()
        frappe.set_user("Administrator")

    def tearDown(self) -> None:
        _clear_column_cache()
        for column in _COLUMNS:
            if frappe.db.has_column("Crema Model Assignment", column):
                # Fixed literal column names — no user input.
                frappe.db.sql_ddl(  # nosemgrep: frappe-sql-format-injection
                    f"alter table `tabCrema Model Assignment` drop column `{column}`"
                )
        _clear_column_cache()
        doc = frappe.get_single("Crema Guardrails")
        for row in doc.guardrails:
            row.action = _GUARDRAIL_BASELINE.get(row.guardrail, "Off")
            row.interfaces = ""
        doc.save(ignore_permissions=True)
        frappe.clear_document_cache("Crema Guardrails")
        frappe.clear_document_cache("Crema Settings")
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — the DDL above already ended the transaction
        super().tearDown()

    def _add_columns(self, *names: str) -> None:
        _clear_column_cache()
        for column in names:
            if not frappe.db.has_column("Crema Model Assignment", column):
                # Fixed literal column names/types from _COLUMNS — no user input.
                frappe.db.sql_ddl(  # nosemgrep: frappe-sql-format-injection
                    f"alter table `tabCrema Model Assignment` add column `{column}` {_COLUMNS[column]}"
                )
        _clear_column_cache()

    @staticmethod
    def _set_legacy(column: str, per_interface: dict[str, object]) -> None:
        for interface, value in per_interface.items():
            # Fixed literal column name — no user input.
            frappe.db.sql(  # nosemgrep: frappe-sql-format-injection
                f"update `tabCrema Model Assignment` set `{column}` = %s"
                " where parent = 'Crema Settings' and interface = %s",
                (value, interface),
            )

    @staticmethod
    def _guardrail_row(key: str):
        return next(r for r in frappe.get_single("Crema Guardrails").guardrails if r.guardrail == key)

    @staticmethod
    def _filter_set(row) -> set[str]:
        return {token.strip() for token in (row.interfaces or "").split(",") if token.strip()}

    @staticmethod
    def _snapshot() -> dict:
        doc = frappe.get_single("Crema Guardrails")
        return {r.guardrail: (r.action, r.interfaces) for r in doc.guardrails}

    def test_fresh_install_without_legacy_columns_changes_nothing(self):
        before = self._snapshot()
        consolidate_guardrails.execute()
        frappe.clear_document_cache("Crema Guardrails")
        self.assertEqual(self._snapshot(), before)

    def test_checkbox_folds_to_block_with_the_enabled_interfaces_as_filter(self):
        self._add_columns("enable_prompt_scan")
        self._set_legacy("enable_prompt_scan", {"simple": 1, "complex": 1})
        consolidate_guardrails.execute()
        scan = self._guardrail_row("scan")
        self.assertEqual(scan.action, "Block")
        self.assertEqual(self._filter_set(scan), {"simple", "complex"})

    def test_filter_stays_blank_when_every_interface_had_the_check_on(self):
        self._add_columns("enable_prompt_scan")
        frappe.db.sql(
            "update `tabCrema Model Assignment` set enable_prompt_scan = 1 where parent = 'Crema Settings'"
        )
        consolidate_guardrails.execute()
        scan = self._guardrail_row("scan")
        self.assertEqual(scan.action, "Block")
        self.assertEqual(scan.interfaces, "")

    def test_ladder_disagreement_folds_to_the_most_severe_value(self):
        self._add_columns("output_trap")
        self._set_legacy("output_trap", {"simple": "Log Only", "complex": "Block"})
        consolidate_guardrails.execute()
        trap = self._guardrail_row("trap")
        self.assertEqual(trap.action, "Block")
        self.assertEqual(self._filter_set(trap), {"simple", "complex"})

    def test_masking_column_folds_onto_pi_and_phi_keeps_its_seeded_default(self):
        self._add_columns("masking")
        self._set_legacy("masking", {"simple": "Log Only"})
        consolidate_guardrails.execute()
        self.assertEqual(self._guardrail_row("pi").action, "Log Only")
        self.assertEqual(self._filter_set(self._guardrail_row("pi")), {"simple"})
        self.assertEqual(self._guardrail_row("phi").action, "Off")


class IntegrationTestCremaRechainCremaLog(CremaFixtureTestCase):
    """crema.patches.rechain_crema_log_for_version_fields — verifies a pre-upgrade
    chain under the frozen old field tuple before re-chaining it under the new one.

    Subclasses CremaFixtureTestCase for its per-test savepoint alone, same reason as
    IntegrationTestCremaLogChain in test_doctypes.py: log.verify_chain walks the whole
    table, so one test's rows surviving into the next would corrupt every later test's
    own "clean chain" precondition."""

    def setUp(self) -> None:
        super().setUp()
        # Same reason as IntegrationTestCremaLogChain: the site's own Crema Log rows
        # sit before this test's rows in the whole-table walk and break the clean
        # pre-upgrade-chain premise. Rolled back by the per-test savepoint.
        frappe.db.delete("Crema Log")

    @staticmethod
    def _insert(prompt_sha: str) -> str:
        log.insert("simple", "test-model", "Success", None, prompt_sha=prompt_sha)
        return frappe.get_doc("Crema Log", {"prompt_sha": prompt_sha}).name

    @staticmethod
    def _simulate_pre_upgrade_chain(names: list[str]) -> None:
        """Overwrite each row's chain_sha with what before_insert would have stamped
        before served_model/app_version existed — simulates a site's real state right
        before this patch runs."""
        from crema.crema.doctype.crema_log.crema_log import chain_hash

        fields = rechain_crema_log_for_version_fields._OLD_CHAIN_FIELDS
        previous = None
        for name in names:
            row = frappe.db.get_value("Crema Log", name, list(fields), as_dict=True)
            new_sha = chain_hash(row, previous, fields=fields)
            frappe.db.set_value("Crema Log", name, "chain_sha", new_sha, update_modified=False)
            previous = new_sha

    def test_a_clean_pre_upgrade_chain_is_rechained_and_verifies(self):
        tag = uuid.uuid4().hex
        names = [self._insert(f"rechain-clean-{i}-{tag}") for i in range(3)]
        self._simulate_pre_upgrade_chain(names)
        self.assertFalse(
            log.verify_chain()["ok"], "sanity: verifying under the new field tuple must fail beforehand"
        )

        rechain_crema_log_for_version_fields.execute()

        result = log.verify_chain()
        self.assertTrue(result["ok"])
        self.assertIsNone(result["first_break"])

    def test_a_break_before_the_upgrade_is_left_untouched(self):
        tag = uuid.uuid4().hex
        names = [self._insert(f"rechain-broken-{i}-{tag}") for i in range(3)]
        self._simulate_pre_upgrade_chain(names)
        frappe.db.set_value(
            "Crema Log", names[1], "detail", "tampered before the upgrade", update_modified=False
        )
        before = [frappe.db.get_value("Crema Log", n, "chain_sha") for n in names]

        with patch("frappe.log_error") as mock_log_error:
            rechain_crema_log_for_version_fields.execute()
        mock_log_error.assert_called_once()

        after = [frappe.db.get_value("Crema Log", n, "chain_sha") for n in names]
        self.assertEqual(before, after, "a pre-existing tamper must not be silently re-hashed away")
