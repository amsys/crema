"""Crema Settings: write the Documents section defaults on a site that existed before
those fields.

frappe applies a field default only when a Single is first created. On an older site the
three new fields read as 0, 0 and "", which would turn off OCR escalation. Only a field
with no stored value gets its default, so an admin's own value stays and a second run
changes nothing.
"""

import frappe

DEFAULTS = {"ocr_max_pages": "20", "ocr_over_limit": "Refuse", "ocr_min_confidence": "70"}


def execute() -> None:
    stored = stored_fields()
    for field, value in DEFAULTS.items():
        if field not in stored:
            frappe.db.set_single_value("Crema Settings", field, value)


def stored_fields() -> set[str]:
    """The Documents fields that already have a row in tabSingles."""
    return {
        row[0]
        for row in frappe.db.sql(
            "select field from `tabSingles` where doctype = 'Crema Settings' and field in %(fields)s",
            {"fields": tuple(DEFAULTS)},
        )
    }
