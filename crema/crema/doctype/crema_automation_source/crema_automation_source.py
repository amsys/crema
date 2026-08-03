"""Crema Automation Source controller — a Crema Automation Task child row.

Per-row validation only, the same split as Crema Model Assignment: everything that can
be decided from one source row lives here, and the cross-row rules (at least one source,
`Update the Records It Read` needs exactly one Document Query, a Document Event trigger needs
at least one) live one level up in CremaAutomationTask.validate.

One of these is load-bearing for security, not merely helpful: a `source_doctype` in the
Crema module is refused, so a task can never read — or be triggered by — crema's own
audit rows.

`source_label` is stamped here rather than derived at render time because a grid column
has to be a real field. It is the "What" column, and it carries the filter count too
("Communication · 2 filters") — a filter count is not worth a column of its own in a grid
with ten to share. crema_automation_task.js stamps the same string on change so the grid
updates before the save.

`source_label` is display only. automation.py needs the same source named without the
filter count — in a prompt header, in a failure line — and calls heading() for that.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

_DEFAULT_SOURCE_LIMIT = 50
_MAX_SOURCE_LIMIT = 200


class CremaAutomationSource(Document):
    def validate(self) -> None:
        if self.source_type == "Document Query":
            self._validate_query()
        else:
            self.source_doctype = None
            self.source_filters = None
            self.last_read = None
            # These four are grid columns now, and a grid cell shows its stored value
            # whatever the row's type is — a `depends_on` on a grid column mutates the
            # shared docfield and so cannot hide a cell per row. Clearing them is what
            # keeps a URL row from claiming a per-run cap it does not have.
            self.source_limit = 0
            self.incremental = 0
            self.read_attachments = 0

        self.source_label = self._label()

    def _validate_query(self) -> None:
        if not self.source_doctype:
            frappe.throw(_("A Document Query source needs a Record Type to Read."))
        if frappe.get_meta(self.source_doctype).module == "Crema":
            frappe.throw(
                _("'{0}' is a Crema doctype — a task cannot read crema's own records.").format(
                    self.source_doctype
                )
            )

        self.source_url = None
        self.source_limit = min(cint(self.source_limit) or _DEFAULT_SOURCE_LIMIT, _MAX_SOURCE_LIMIT)

        try:
            # A trial query is the cheapest way to reject a bad fieldname/operator/shape
            # now, with frappe's own error message, instead of at 3am in last_error.
            frappe.get_all(self.source_doctype, filters=self.parsed_filters(), limit=1)
        except frappe.ValidationError:
            raise
        except Exception as exc:
            frappe.throw(_("Which Records is not usable on {0}: {1}").format(self.source_doctype, str(exc)))

    def parsed_filters(self) -> dict | list:
        raw = (self.source_filters or "").strip()
        if not raw:
            return []
        try:
            filters = frappe.parse_json(raw)
        except Exception as exc:
            frappe.throw(_("Which Records is not valid JSON: {0}").format(str(exc)))
        if not isinstance(filters, dict | list):
            frappe.throw(_("Which Records must be a JSON list or object."))
        return filters

    def heading(self) -> str:
        """This source named plainly — no filter count. automation._read_source puts it in
        the `=== Source: ... ===` header the model reads, and in the run's own failure and
        note lines, where "· 2 filters" would be noise."""
        if self.source_type == "Document Query":
            return self.source_doctype or ""
        # The scheme is noise in a grid cell and every URL here has one.
        return (self.source_url or "").split("://", 1)[-1]

    def _label(self) -> str:
        """The What grid column: heading(), plus the filter count when there is one."""
        heading = self.heading()
        if self.source_type != "Document Query":
            return heading
        count = len(self.parsed_filters())
        if not count:
            return heading
        return f"{heading} · {count} filter" + ("s" if count > 1 else "")
