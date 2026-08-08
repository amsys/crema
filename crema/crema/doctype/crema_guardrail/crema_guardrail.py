"""Crema Guardrail controller — a Crema Guardrails child row.

Per-row rules live one level up, in CremaGuardrails.validate (crema_guardrails.py):
the row set is reconciled against crema.guardrails.registry(), the use-case filter is
validated there, and the AI Guard row's executor check needs the whole document.
"""

from __future__ import annotations

from frappe.model.document import Document


class CremaGuardrail(Document):
    pass
