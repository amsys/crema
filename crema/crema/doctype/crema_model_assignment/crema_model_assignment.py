"""Crema Model Assignment controller — a Crema Settings child row.

Per-row validation only. The cross-row rules (the interfaces.names() row set, the
provider/isolation-user pairing) live one level up, in CremaSettings.validate — see
crema_settings.py.
"""

from __future__ import annotations

import frappe
from crema import interfaces
from frappe.model.document import Document


def validate_isolation_user(user: str, label: str) -> None:
    """The fencing rules an isolation user (row-level or Crema Settings'
    default_isolation_user) must satisfy. Pulled out of CremaModelAssignment.validate
    so CremaSettings.validate can run the exact same checks on default_isolation_user
    — a blank field is valid here (a row/default pair with no provider needs none);
    the "provider requires isolation_user" pairing rule lives one level up, since it's
    a cross-field (row) or cross-field-on-the-Single (default) question this function
    can't answer on its own."""
    if not user:
        return

    if user == "Administrator":
        frappe.throw(f"{label} must not be Administrator — the sandbox would be theater.")

    if not frappe.db.get_value("User", user, "enabled"):
        frappe.throw(f"{label} '{user}' must be an enabled user.")

    if "System Manager" in frappe.get_roles(user):
        frappe.throw(
            f"{label} '{user}' has the System Manager role, which dissolves the sandbox: it "
            "grants full read/write on Crema Provider (including api_key) and every other "
            "doctype. Use a dedicated, fenced, low-privilege account whose roles and User "
            "Permissions define exactly what this interface may touch."
        )


class CremaModelAssignment(Document):
    def validate(self) -> None:
        validate_isolation_user(self.isolation_user, f"'{self.interface}': Isolation User")

        if not self.system_prompt:
            self.system_prompt = interfaces.prompt_for(self.interface)
