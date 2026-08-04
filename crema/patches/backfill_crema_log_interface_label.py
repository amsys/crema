"""Crema Log: fill interface_label on rows written before the field existed.

interface_label is now the doctype's title_field, so without this every existing row
renders with a blank subject in the list until it ages out of log_retention_days.

Two steps, and the order matters: seed every empty label from the raw key first, so an
app-registered interface (which interfaces.LABELS deliberately does not cover — LABELS is
core-only) keeps its key as its label, then overwrite the core names with their labels.
"""

import frappe
from crema import interfaces


def execute() -> None:
    if not frappe.db.has_column("Crema Log", "interface_label"):
        return

    frappe.db.sql(
        """
        update `tabCrema Log`
        set interface_label = interface
        where ifnull(interface_label, '') = '' and ifnull(interface, '') != ''
        """
    )
    for name, label in interfaces.LABELS.items():
        frappe.db.sql(
            "update `tabCrema Log` set interface_label = %(label)s where interface = %(name)s",
            {"label": label, "name": name},
        )
