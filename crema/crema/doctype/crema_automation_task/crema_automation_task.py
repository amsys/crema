"""Crema Automation Task controller."""

from __future__ import annotations

from croniter import croniter

import frappe
from crema import interfaces
from frappe.model.document import Document


class CremaAutomationTask(Document):
    def validate(self) -> None:
        if not croniter.is_valid(self.schedule or ""):
            frappe.throw(f"'{self.schedule}' is not a valid cron expression.")

        if self.interface not in interfaces.PREDEFINED:
            frappe.throw(f"'{self.interface}' is not a known crema interface.")
