"""Crema Settings controller — the Single that replaces the old Crema Interface
doctype and the old 'Crema Settings' Page.

Owns the cross-row rules the child doctype (Crema Model Assignment) can't see on its
own: the fixed PREDEFINED row set, the 'security' recursion guard, and the
llm-guard-requires-a-configured-security-interface check.
"""

from __future__ import annotations

import frappe
from crema import cache, interfaces
from crema.crema.doctype.crema_model_assignment.crema_model_assignment import validate_isolation_user
from frappe.model.document import Document


class CremaSettings(Document):
    def validate(self) -> None:
        self._reconcile_assignments()
        # Frappe does NOT call a child row's own controller validate() as part of a
        # parent save — Document._validate() only runs generic field-level checks
        # (_validate_data_fields, _validate_selects, ...) on children; run_method
        # ("validate") is invoked on the parent only. CremaModelAssignment.validate
        # (per-row isolation-user fencing, default system_prompt) has to be called
        # explicitly here, or it silently never runs.
        for row in self.assignments:
            row.validate()
        validate_isolation_user(self.default_isolation_user, "Default Isolation User")
        self._validate_provider_isolation_pairing()
        self._apply_security_guard_rules()
        self._warn_missing_models()

    def _validate_provider_isolation_pairing(self) -> None:
        """A provider and an isolation user are required together — evaluated on
        *effective* values, since either half can now come from a Default. A row that
        overrides only its provider still needs an isolation user from somewhere (its
        own override or default_isolation_user); a row that overrides neither is fine
        either way, and one that overrides only isolation_user with no effective
        provider has nothing to sandbox yet."""
        for row in self.assignments:
            effective_provider = row.provider or self.default_provider
            effective_isolation_user = row.isolation_user or self.default_isolation_user
            if effective_provider and not effective_isolation_user:
                frappe.throw(
                    f"'{row.interface}': Isolation User is required once a Provider is set "
                    "(row override or Default Isolation User)."
                )

    def _reconcile_assignments(self) -> None:
        """Keep `assignments` to exactly one row per interfaces.PREDEFINED name, in
        that order — append a missing name, drop an unknown one, reorder the rest.
        This is what makes the set "all present and fixed"; it replaces the old
        install.sync_interfaces insert loop."""
        by_name = {row.interface: row for row in self.assignments if row.interface}
        self.assignments = []
        for name in interfaces.PREDEFINED:
            row = by_name.get(name)
            if row is None:
                row = self.append("assignments", {})
                row.interface = name
            else:
                self.assignments.append(row)
            row.interface_label = interfaces.LABELS.get(name, name)

    def _warn_missing_models(self) -> None:
        """A provider with no model still resolves (client._load_from_db has no fence
        here) and produces a broken litellm model string at call time — warn at save
        time rather than block, since a mid-setup save (provider chosen, model not
        picked yet) is a normal, valid state."""
        missing = [
            row.interface
            for row in self.assignments
            if (row.provider or self.default_provider) and not (row.model or self.default_model)
        ]
        if missing:
            lines = "".join(f"<li>'{name}': no Model set (row or Default Model).</li>" for name in missing)
            frappe.msgprint(f"<ul>{lines}</ul>", indicator="orange", title="Missing Model")

    def _apply_security_guard_rules(self) -> None:
        by_name = {row.interface: row for row in self.assignments}

        security_row = by_name.get("security")
        if security_row is not None:
            # Recursion guard: the security interface can't guard itself.
            security_row.enable_llm_guard = 0

        # "security" has no fallback entry (interfaces.FALLBACKS) but IS covered by
        # default_provider like every other interface — see client._load_from_db.
        security_configured = bool(security_row and (security_row.provider or self.default_provider))
        for row in self.assignments:
            if row.interface == "security" or not row.enable_llm_guard:
                continue
            if not security_configured:
                frappe.throw(
                    f"'{row.interface}': enabling the LLM Guard requires a 'security' "
                    "interface to be configured first."
                )

    def on_update(self) -> None:
        cache.clear_interfaces()
