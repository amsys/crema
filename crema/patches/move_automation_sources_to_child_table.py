"""Move each Crema Automation Task's single source into the `sources` child table.

Runs post_model_sync: the Crema Automation Source table does not exist in the database
until the model sync creates it. The old flat columns on the parent are gone from the
doctype JSON by then but not from the table — frappe never drops a column on its own
(frappe/model/meta.py's trim_tables is a separate, manual command) — so they are read here
with a raw query rather than through the meta.

`last_run` used to double as the incremental watermark. It is now the cron cursor only, so
it is copied into the new row's `last_read`: without that, every incremental task would
re-read (and re-bill for) its whole backlog on the first run after this patch.
"""

import frappe
from frappe.model.document import bulk_insert
from frappe.utils import cint, now

_COLUMNS = ("source_type", "source_url", "source_doctype", "source_filters", "source_limit", "incremental")


def _note(task) -> str:
    try:
        count = len(frappe.parse_json(task.source_filters or "[]"))
    except Exception:
        count = 0
    parts = ["no filters" if not count else "1 filter" if count == 1 else f"{count} filters"]
    parts.append(f"{cint(task.source_limit) or 50}/run")
    if task.incremental:
        parts.append("only changed")
    return " · ".join(parts)


def execute():
    if not frappe.db.has_column("Crema Automation Task", "source_type"):
        return  # fresh install — the flat fields never existed

    migrated = {
        row.parent
        for row in frappe.get_all(
            "Crema Automation Source", filters={"parenttype": "Crema Automation Task"}, fields=["parent"]
        )
    }
    # _COLUMNS is a module constant of literal column names — no user input reaches this.
    tasks = frappe.db.sql(  # nosemgrep: frappe-sql-format-injection
        f"select name, last_run, {', '.join(_COLUMNS)} from `tabCrema Automation Task`",
        as_dict=True,
    )

    stamp = now()
    rows = []
    for task in tasks:
        if task.name in migrated:
            continue
        is_query = task.source_type == "Document Query"
        source = frappe.get_doc(
            {
                "doctype": "Crema Automation Source",
                "parent": task.name,
                "parenttype": "Crema Automation Task",
                "parentfield": "sources",
                "idx": 1,
                "source_type": task.source_type or "URL",
                "source_url": task.source_url,
                "source_doctype": task.source_doctype,
                "source_filters": task.source_filters,
                "source_limit": task.source_limit,
                "incremental": task.incremental,
                "last_read": task.last_run if is_query else None,
                # Same two strings CremaAutomationSource.validate stamps, so the grid reads
                # right before anyone re-saves the task.
                "source_label": (task.source_doctype or "")
                if is_query
                else (task.source_url or "").split("://", 1)[-1],
                "source_note": _note(task) if is_query else "",
                "owner": "Administrator",
                "modified_by": "Administrator",
                "creation": stamp,
                "modified": stamp,
            }
        )
        source.set_new_name()
        rows.append(source)

    if rows:
        bulk_insert("Crema Automation Source", rows)
