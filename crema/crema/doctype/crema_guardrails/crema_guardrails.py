"""Crema Guardrails controller — the Single holding the ordered guardrail list.

Owns the rules a child row can't see on its own: the use-case filter tokens, and each
AI Guard row's needs-a-working-provider check (which replaced the old
llm-guard-requires-a-configured-security-interface rule on Crema Settings). Friendly
labels for the registry (built-ins plus every app-registered guardrail) come from
crema.guardrails.label_for — display only, never stored.

Unlike Crema Settings' `assignments` (one row per interface, always, membership fixed
by CremaSettings._reconcile_assignments), this table is the user's to edit: add a row
to run a check again or add one this site has installed, delete a row to stop running
it, drag to reorder — row order IS execution order (crema.guardrails.run). A deleted
row has the same effect as Off; nothing here re-adds it.
"""

# Note on the one case this doctype DOES drop a row without being asked: the
# Guardrail field is a Select, its options stamped from crema.guardrails.registry()
# by install.sync_guardrail_options. Frappe re-validates EVERY child row's Select
# value on EVERY save of the parent (frappe.model.document.Document.validate calls
# _validate_selects() over self.get_all_children(), not just the rows a save touched)
# — so a row whose app was uninstalled after it was added would make the WHOLE Single
# unsavable, forever, until someone removed it by hand. _drop_orphans below is the
# fix: it removes exactly that row, and only that row, before Frappe's own Select
# check ever runs. This is not a return of the old auto-reconcile (nothing here
# APPENDS a missing row) — it is what keeps "delete a row to turn it off" true even
# for a row an app uninstall orphaned instead of an admin.

from __future__ import annotations

import frappe
from crema import guardrails as _guardrails
from crema import interfaces
from crema.exceptions import CremaConfigError
from frappe import _
from frappe.model.document import Document


class CremaGuardrails(Document):
    def validate(self) -> None:
        self._drop_orphans()
        self._validate_filters()
        self._validate_guard()
        self._warn_audio()

    def _drop_orphans(self) -> None:
        """Remove a row whose guardrail key no longer resolves in the registry — the
        one case this doctype drops a row unasked, and only because leaving it would
        make the whole Single unsavable (see the module-level note above). Not a
        return of the old reconcile-to-registry: nothing here appends a missing row,
        and a row an admin's own delete removed stays removed."""
        self.guardrails = [row for row in self.guardrails if _guardrails._module(row.guardrail)]

    def _validate_filters(self) -> None:
        valid = set(interfaces.names())
        for row in self.guardrails:
            tokens = [t.strip() for t in (row.interfaces or "").split(",") if t.strip()]
            unknown = [t for t in tokens if t not in valid]
            if unknown:
                frappe.throw(
                    _("'{0}': unknown use case '{1}'. Leave Use Cases empty to apply everywhere.").format(
                        _guardrails.label_for(row.guardrail), unknown[0]
                    )
                )
            row.interfaces = ", ".join(tokens)

    def _validate_guard(self) -> None:
        """Every non-Off AI Guard row (there may be more than one) that cannot resolve
        a working provider fails loud at save time, not silently at call time — same
        intent as the old Crema Settings rule, moved here with the guard itself."""
        from crema import client

        for row in self.guardrails:
            if row.guardrail != "llm_guard" or (row.action or "Off") == "Off":
                continue
            try:
                client._scanner_cfg(row.guard_provider, row.guard_model)
            except CremaConfigError:
                frappe.throw(
                    _(
                        "The AI Guard row '{0}' needs a working AI service to run on. Give it a "
                        "Checked By and a Guard Model, set a Default Provider in Crema Settings, "
                        "or set this row to Off."
                    ).format(_guardrails.label_for(row.guardrail))
                )

    def _warn_audio(self) -> None:
        """Audio has no text to scan or mask before it is sent — warn (don't block) when
        a row explicitly names the transcribe use case. A blank filter (= everywhere)
        does not warn; that would fire on every save."""
        flagged = [
            _guardrails.label_for(row.guardrail)
            for row in self.guardrails
            if (row.action or "Off") != "Off"
            and "transcribe" in [t.strip() for t in (row.interfaces or "").split(",")]
        ]
        if flagged:
            frappe.msgprint(
                _(
                    "Audio cannot be checked or masked before it is sent. For the Transcribe "
                    "use case, only a Hide guardrail set to Block has any effect — it refuses "
                    "the call. Affected: {0}."
                ).format(", ".join(flagged)),
                indicator="orange",
                title=_("Guardrails and audio"),
            )
