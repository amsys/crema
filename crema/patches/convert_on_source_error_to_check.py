"""Crema Automation Task.on_source_error: Select -> Check.

The field was two options, "Skip and Continue" and "Fail the Run" — a boolean wearing a
dropdown, and one wide enough to need its own CSS rule to render at a sensible width.

This runs in [pre_model_sync], before frappe alters the column from varchar to int. That
order is the whole point: MariaDB casts a non-numeric string to 0, so migrating first
would silently turn every "Fail the Run" into "skip and continue" — a task that was told
to stop on a bad source would quietly run on partial content instead.

The fieldname is kept. Renaming it to read as a boolean would cost a second patch to move
the column, for nothing a label does not already say.
"""

import frappe


def execute() -> None:
    if not frappe.db.has_column("Crema Automation Task", "on_source_error"):
        return

    frappe.db.sql(
        """
        update `tabCrema Automation Task`
        set on_source_error = if(on_source_error = 'Fail the Run', '1', '0')
        """
    )
