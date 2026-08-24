"""Delete AI Guard rows from Crema Guardrails.

The AI Guard (guardrail key "llm_guard") is gone from crema.guardrails._BUILTINS —
its check now belongs on the gateway, not in crema. A site that had an AI Guard row
keeps an orphaned "Crema Guardrail" child row; CremaGuardrails._drop_orphans would
remove it lazily on the next save of the Single, but this patch removes it now, so a
site that never re-saves the Single still loses the dead row.

The guardrail Select options refresh anyway on this same migrate, through
install.sync_guardrail_options.
"""

from __future__ import annotations

import frappe


def execute() -> None:
    if not frappe.db.has_column("Crema Guardrail", "guardrail"):
        return

    frappe.db.delete("Crema Guardrail", {"guardrail": "llm_guard"})
