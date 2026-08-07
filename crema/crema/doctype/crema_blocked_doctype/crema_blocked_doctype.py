"""Crema Blocked Doctype controller — a Crema Settings child row. No row-level
validation of its own: the Link field to DocType already refuses a name that doesn't
exist, and a duplicate row is redundant but harmless. See automation._validate_plan and
crema.policy for what actually enforces the list."""

from __future__ import annotations

from frappe.model.document import Document


class CremaBlockedDoctype(Document):
    pass
