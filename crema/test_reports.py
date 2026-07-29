"""Integration tests for the Crema Usage script report.

`group_by` reaches a raw SQL GROUP BY clause via a fixed whitelist — the one thing
here worth pinning against regression is that an unlisted value is rejected outright,
never interpolated.
"""

from __future__ import annotations

import frappe
from crema.crema.report.crema_usage.crema_usage import execute
from frappe.tests import IntegrationTestCase


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
