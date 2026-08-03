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
    """Clear the per-run cap and the two checks on a URL row.

    The Details column became several of its own, and a URL row still holds a
    records-per-run cap it never used — which the grid would now display as fact. These are
    display-only values that CremaAutomationSource.validate regenerates on the next save;
    this only stops the grid lying in the meantime. (The same pass used to re-stamp a
    `source_note` column; that field has since been folded into `source_label`, and
    validate regenerates that one too.)
    """
    source = frappe.qb.DocType("Crema Automation Source")
    (
        frappe.qb.update(source)
        .set(source.source_limit, 0)
        .set(source.incremental, 0)
        .set(source.read_attachments, 0)
        .where(source.source_type != "Document Query")
        .run()
    )
