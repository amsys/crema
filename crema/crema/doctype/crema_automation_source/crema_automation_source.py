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
# A File Query bills at least two AI calls per file (OCR, then extraction), against one
# per record for a Document Query — a lower default and cap keep an unattended run's cost
# in the same ballpark.
_DEFAULT_FILE_SOURCE_LIMIT = 10
_MAX_FILE_SOURCE_LIMIT = 50

_FILE_DOCTYPE = "File"


class CremaAutomationSource(Document):
    def validate(self) -> None:
        if self.source_type == "Document Query":
            if not self.source_doctype:
                frappe.throw(_("A Document Query source needs a Record Type to Read."))
            self._validate_query(self.source_doctype, _DEFAULT_SOURCE_LIMIT, _MAX_SOURCE_LIMIT)
            self.read_attachments = cint(self.read_attachments)
            self.read_children = cint(self.read_children)
        elif self.source_type == "File Query":
            # Pinned, not admin-chosen: a File Query always reads the File doctype itself
            # (the bytes), never a doctype's own fields — that is what a Document Query is
            # for. Pinning it here is also what makes a Document Event trigger work on a
            # File Query for free: _event_tasks maps (doctype, event) -> task, and this
            # gives it "File" to key on with no extra code.
            self._validate_query(_FILE_DOCTYPE, _DEFAULT_FILE_SOURCE_LIMIT, _MAX_FILE_SOURCE_LIMIT)
            self.read_attachments = 0  # a File Query IS the file read; nothing to attach
            self.read_children = 0  # a File Query reads bytes, not a doctype's own fields
        else:
            self.source_doctype = None
            self.source_filters = None
            self.last_read = None
            # These five are grid columns now, and a grid cell shows its stored value
            # whatever the row's type is — a `depends_on` on a grid column mutates the
            # shared docfield and so cannot hide a cell per row. Clearing them is what
            # keeps a URL row from claiming a per-run cap it does not have.
            self.source_limit = 0
            self.incremental = 0
            self.read_attachments = 0
            self.read_children = 0

        self.source_label = self._label()

    def _validate_query(self, doctype: str, default_limit: int, max_limit: int) -> None:
        self.source_doctype = doctype
        if frappe.get_meta(doctype).module == "Crema":
            frappe.throw(
                _("'{0}' is a Crema doctype — a task cannot read crema's own records.").format(doctype)
            )

        self.source_url = None
        self.source_limit = min(cint(self.source_limit) or default_limit, max_limit)

        try:
            # A trial query is the cheapest way to reject a bad fieldname/operator/shape
            # now, with frappe's own error message, instead of at 3am in last_error.
            frappe.get_all(doctype, filters=self.parsed_filters(), limit=1)
        except frappe.ValidationError:
            raise
        except Exception as exc:
            frappe.throw(_("Which Records is not usable on {0}: {1}").format(doctype, str(exc)))

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
        if self.source_type == "File Query":
            return "Files"
        # The scheme is noise in a grid cell and every URL here has one.
        return (self.source_url or "").split("://", 1)[-1]

    def _label(self) -> str:
        """The What grid column: heading(), plus the filter count when there is one."""
        heading = self.heading()
        if self.source_type not in ("Document Query", "File Query"):
            return heading
        count = len(self.parsed_filters())
        if not count:
            return heading
        return f"{heading} · {count} filter" + ("s" if count > 1 else "")
