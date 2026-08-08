"""Crema Settings controller — the Single that replaces the old Crema Interface
doctype and the old 'Crema Settings' Page.

Owns the cross-row rules the child doctype (Crema Model Assignment) can't see on its
own: the interfaces.names() row set (core PREDEFINED + app-registered) and the
provider/isolation-user pairing. The security-pipeline rules moved to the Crema
Guardrails doctype (crema_guardrails.py) with the pipeline itself.
"""

from __future__ import annotations

import frappe
from crema import cache, interfaces
from crema.crema.doctype.crema_model_assignment.crema_model_assignment import validate_isolation_user
from frappe import _
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
        """Keep `assignments` to exactly one row per interfaces.names() name
        (PREDEFINED plus every app-registered interface — see the `crema_interfaces`
        hooks.py key), in that order — append a missing name, drop an unknown one,
        reorder the rest. This is what makes the set "all present and fixed"; it
        replaces the old install.sync_interfaces insert loop. A newly-created
        app-registered row is seeded from its hook config (any Crema Model Assignment
        fieldname — max_tokens, cache_ttl, ...) once, so an
        admin's later edits in the desk survive the next reconcile. "prompt", "fallback"
        and "label" are read separately (interfaces.prompt_for/fallback_for/label_for),
        not seeded onto the row: none of the three is a Crema Model Assignment
        fieldname."""
        by_name = {row.interface: row for row in self.assignments if row.interface}
        self.assignments = []
        app_cfg = interfaces.app_interfaces()
        for name in interfaces.names():
            row = by_name.get(name)
            if row is None:
                row = self.append("assignments", {})
                row.interface = name
                for field, value in app_cfg.get(name, {}).items():
                    if field not in ("prompt", "fallback", "label"):
                        setattr(row, field, value)
            else:
                self.assignments.append(row)
            row.interface_label = interfaces.label_for(name)

    def _warn_missing_models(self) -> None:
        """A provider with no model still resolves (client._load_from_db has no fence
        here) and produces a broken litellm model string at call time — warn at save
        time rather than block, since a mid-setup save (provider chosen, model not
        picked yet) is a normal, valid state.

        Skipped when crema_skip_model_warning is set — the nested settings.save()
        inside crema_provider.create_from_template (and CremaProvider.on_trash) sets
        default_provider (or clears it) on the admin's behalf; with no model anywhere
        yet, every interface would qualify as "missing" on a save the admin never
        made. A save the admin actually makes on this form still warns."""
        if self.flags.crema_skip_model_warning:
            return
        missing = [
            row.interface_label or row.interface
            for row in self.assignments
            if (row.provider or self.default_provider) and not (row.model or self.default_model)
        ]
        if missing:
            item = _("'{0}': no Model set (row or Default Model).")
            lines = "".join(f"<li>{item.format(name)}</li>" for name in missing)
            frappe.msgprint(f"<ul>{lines}</ul>", indicator="orange", title=_("Missing Model"))

    def on_update(self) -> None:
        cache.clear_interfaces()
