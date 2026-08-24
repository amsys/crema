"""Drop the monthly_budget_usd column from Crema Provider.

A per-provider budget is gone from crema.crema.doctype.crema_provider.crema_provider.json
— a provider is an infrastructure identity, and that ceiling now belongs on the gateway
in front of it, not in crema. Frappe never drops a removed column on its own, so this
patch drops it directly.
"""

from __future__ import annotations

import frappe


def execute() -> None:
    if not frappe.db.has_column("Crema Provider", "monthly_budget_usd"):
        return

    frappe.db.sql("alter table `tabCrema Provider` drop column monthly_budget_usd")
