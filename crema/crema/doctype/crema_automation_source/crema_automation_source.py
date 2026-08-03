"""Crema Automation Source controller — a Crema Automation Task child row.

Per-row validation only, the same split as Crema Model Assignment: everything that can
be decided from one source row lives here, and the cross-row rules (at least one source,
`Update Source Records` needs exactly one Document Query, a Document Event trigger needs
at least one) live one level up in CremaAutomationTask.validate.

One of these is load-bearing for security, not merely helpful: a `source_doctype` in the
Crema module is refused, so a task can never read — or be triggered by — crema's own
audit rows.

`source_label` and `source_note` are stamped here rather than derived at render time
because a grid column has to be a real field. They are display only; nothing in
automation.py reads them for behaviour. crema_automation_task.js stamps the same two
strings on change so the grid updates before the save.
"""

from __future__ import annotations

import frappe
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

        self.source_label = self._label()
        self.source_note = self._note()

    def _validate_query(self) -> None:
        if not self.source_doctype:
            frappe.throw("A Document Query source needs a Record Type to Read.")
        if frappe.get_meta(self.source_doctype).module == "Crema":
            frappe.throw(
                f"'{self.source_doctype}' is a Crema doctype — a task cannot read crema's own records."
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
            frappe.throw(f"Which Records is not usable on {self.source_doctype}: {exc}")

    def parsed_filters(self) -> dict | list:
        raw = (self.source_filters or "").strip()
        if not raw:
            return []
        try:
            filters = frappe.parse_json(raw)
        except Exception as exc:
            frappe.throw(f"Which Records is not valid JSON: {exc}")
        if not isinstance(filters, dict | list):
            frappe.throw("Which Records must be a JSON list or object.")
        return filters

    def _label(self) -> str:
        if self.source_type == "Document Query":
            return self.source_doctype or ""
        # The scheme is noise in a 4-column grid cell and every URL here has one.
        return (self.source_url or "").split("://", 1)[-1]

    def _note(self) -> str:
        if self.source_type != "Document Query":
            return ""
        count = len(self.parsed_filters())
        parts = ["no filters" if not count else "1 filter" if count == 1 else f"{count} filters"]
        parts.append(f"{cint(self.source_limit) or _DEFAULT_SOURCE_LIMIT}/run")
        if self.incremental:
            parts.append("only changed")
        return " · ".join(parts)
