"""Add the Reply Filter row to an already-seeded Crema Guardrails Single.

crema.install.sync_guardrails only seeds a fresh site — a site already `seeded` never
gets a builtin added after the fact, so the new "reply" guardrail (crema.guardrails.
_ReplyFilter) would otherwise never appear on an existing site. Insert-only, and a
no-op once the row exists, so an admin's own delete of it is never undone (the same
contract CremaGuardrails' own docstring states for every other row).

Seeded right after the Text Scan row (position 0 if there is somehow no Text Scan row),
never at the end: after() hooks run in REVERSE row order (crema.guardrails.run), so a
row near the top has its after() run near the LAST — the position the Reply Filter
needs to see the fully mask-restored, nonce-stripped reply, not an intermediate form.
"""

from __future__ import annotations

import frappe
from crema import install


def execute() -> None:
    # Patches run before hooks.after_migrate, so the Guardrail Select doesn't know
    # "reply" yet — doc.save() below would fail _validate_selects without this.
    install.sync_guardrail_options()

    doc = frappe.get_single("Crema Guardrails")
    if not doc.seeded:
        return  # a fresh site: after_migrate's sync_guardrails seeds every builtin
    if any(row.guardrail == "reply" for row in doc.guardrails):
        return  # already present — an admin's row, or a re-run of this same patch

    scan_idx = next((i for i, row in enumerate(doc.guardrails) if row.guardrail == "scan"), -1)
    new_row = doc.append("guardrails", {"guardrail": "reply", "action": "Off"})  # appends at the end
    doc.guardrails.remove(new_row)
    doc.guardrails.insert(scan_idx + 1, new_row)
    for position, row in enumerate(doc.guardrails, start=1):
        row.idx = position
    doc.save(ignore_permissions=True)
