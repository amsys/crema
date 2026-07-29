"""Crema Log controller — no custom logic; records are inserted with
ignore_permissions=True from crema/api.py and are otherwise read/delete only."""

from __future__ import annotations

from frappe.model.document import Document


class CremaLog(Document):
    pass
