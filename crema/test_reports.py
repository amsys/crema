"""Integration tests for the Crema Usage script report.

`group_by` reaches a raw SQL GROUP BY clause via a fixed whitelist — the one thing
here worth pinning against regression is that an unlisted value is rejected outright,
never interpolated.
"""

from __future__ import annotations

import uuid

import frappe
from crema.crema.report.crema_usage.crema_usage import execute
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime


class IntegrationTestCremaUsageReport(IntegrationTestCase):
    def setUp(self) -> None:
        super().setUp()
        frappe.set_user("Administrator")

    def test_unknown_group_by_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            execute({"group_by": "1; DROP TABLE `tabCrema Log`; --"})

    def test_default_group_by_is_interface(self):
        columns, data, message, chart = execute({})
        self.assertEqual(columns[0]["label"], "Interface")
        self.assertIsInstance(data, list)
        self.assertIsNone(message)
        self.assertEqual(chart["type"], "bar")

    def test_group_by_day_uses_the_date_function(self):
        columns, *_rest = execute({"group_by": "Day"})
        self.assertEqual(columns[0]["label"], "Day")

    def test_group_by_model_uses_the_model_field(self):
        columns, *_rest = execute({"group_by": "Model"})
        self.assertEqual(columns[0]["label"], "Model")

    def test_group_by_user_uses_the_user_field(self):
        columns, *_rest = execute({"group_by": "User"})
        self.assertEqual(columns[0]["label"], "User")


class IntegrationTestCremaUsageReportWhere(IntegrationTestCase):
    """_where's four filters, pinned by seeding rows that differ in exactly the field
    under test and asserting the non-matching row is excluded from the result.

    Plain IntegrationTestCase, matching the sibling class above: nothing here calls
    frappe.db.commit(), so the rows seeded by _seed() are undone by the single
    class-level rollback IntegrationTestCase already registers (see
    test_client.py's CremaFixtureTestCase docstring for the full explanation) —
    the same pattern test_automation.py's test_cleanup_logs_deletes_old_rows_and_keeps_
    recent_ones relies on.
    """

    def setUp(self) -> None:
        super().setUp()
        frappe.set_user("Administrator")

    def _seed(self, interface: str, status: str, days_ago: int = 0):
        doc = frappe.get_doc({"doctype": "Crema Log", "interface": interface, "status": status}).insert(
            ignore_permissions=True
        )
        if days_ago:
            frappe.db.set_value(
                "Crema Log", doc.name, "creation", add_to_date(now_datetime(), days=-days_ago)
            )
        return doc

    def test_interface_filter_excludes_other_interfaces(self):
        keep = f"_test_crema_report_{uuid.uuid4().hex[:8]}"
        drop = f"_test_crema_report_{uuid.uuid4().hex[:8]}"
        self._seed(keep, "Success")
        self._seed(drop, "Success")

        _columns, data, _message, _chart = execute({"interface": keep})

        group_values = {row["group_value"] for row in data}
        self.assertIn(keep, group_values)
        self.assertNotIn(drop, group_values)

    def test_status_filter_excludes_other_statuses(self):
        interface = f"_test_crema_report_{uuid.uuid4().hex[:8]}"
        self._seed(interface, "Success")
        self._seed(interface, "Blocked")

        _columns, data, _message, _chart = execute({"interface": interface, "status": "Blocked"})

        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["calls"], 1)
        self.assertEqual(data[0]["blocked"], 1)

    def test_from_date_filter_excludes_older_rows(self):
        interface = f"_test_crema_report_{uuid.uuid4().hex[:8]}"
        self._seed(interface, "Success")  # creation = now
        self._seed(interface, "Success", days_ago=40)

        _columns, data, _message, _chart = execute(
            {
                "interface": interface,
                "from_date": add_to_date(now_datetime(), days=-1).strftime("%Y-%m-%d"),
            }
        )

        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["calls"], 1)

    def test_to_date_filter_excludes_newer_rows(self):
        interface = f"_test_crema_report_{uuid.uuid4().hex[:8]}"
        self._seed(interface, "Success")  # creation = now
        self._seed(interface, "Success", days_ago=40)

        _columns, data, _message, _chart = execute(
            {
                "interface": interface,
                "to_date": add_to_date(now_datetime(), days=-1).strftime("%Y-%m-%d"),
            }
        )

        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["calls"], 1)
