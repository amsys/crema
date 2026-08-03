"""Rename the Crema Automation Task `action` values to plain English.

`action` is a Select, so the option string is both the stored value and the label — there
is no way to relabel one without rewriting the data. The three names were jargon:
"Upsert Records" is not a phrase a non-technical admin uses, and "Report Only" conflated
"write nothing" with "email me" (any action may email its result now).

Runs post_model_sync, so the doctype already carries the new options; until this rewrite
an existing row simply holds a value outside them, which frappe only rejects on save.
"""

from __future__ import annotations

import frappe

RENAMES = {
    "Upsert Records": "Create or Update Records",
    "Update Source Records": "Update the Records It Read",
    "Report Only": "No Changes",
}


def execute() -> None:
    task = frappe.qb.DocType("Crema Automation Task")
    for old, new in RENAMES.items():
        frappe.qb.update(task).set(task.action, new).where(task.action == old).run()

    _restamp_sources()


def _restamp_sources() -> None:
    """Re-stamp the source rows' grid cells for the new columns.

    The Details column became four: Filters, Records Per Run, Only Changed, Attachments.
    An existing row still holds the old combined summary in `source_note` ("no filters ·
    50/run · only changed") and, on a URL row, a records-per-run cap it never used — both
    of which the grid would now display as fact. These are display-only values that
    CremaAutomationSource.validate regenerates on the next save; this only stops the grid
    lying in the meantime.
    """
    source = frappe.qb.DocType("Crema Automation Source")
    (
        frappe.qb.update(source)
        .set(source.source_limit, 0)
        .set(source.incremental, 0)
        .set(source.read_attachments, 0)
        .set(source.source_note, "")
        .where(source.source_type != "Document Query")
        .run()
    )

    for row in frappe.get_all(
        "Crema Automation Source", filters={"source_type": "Document Query"}, pluck="name"
    ):
        doc = frappe.get_doc("Crema Automation Source", row)
        frappe.db.set_value("Crema Automation Source", row, "source_note", doc._note(), update_modified=False)
