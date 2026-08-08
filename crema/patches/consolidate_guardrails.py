"""Move the four per-interface safety fields onto the Crema Guardrails list.

The fields (enable_prompt_scan, enable_llm_guard, output_trap, masking) are gone from
Crema Model Assignment; their orphaned columns are still in the table (frappe never
drops a removed column), so this reads them straight off `tabCrema Model Assignment`
— each guarded by has_column, since `masking` only ever existed on the experimental
branch and a fresh install has none of the four.

One Guardrails row cannot express per-interface ACTIONS, only a per-interface filter:
where the old rows disagreed on a ladder value (output_trap Block here, Log Only
there), the MOST SEVERE value wins and the filter is the union of every interface that
had the check on. The old security row's custom system_prompt (if any) becomes the AI
Guard row's guard_prompt, and its provider/model become the AI Guard row's
guard_provider/guard_model — the AI Guard now owns a provider directly rather than
borrowing an interface's, so this DOES map, unlike the interface name the old
"Checked By" picker needed. The security assignment row itself is removed by
CremaSettings._reconcile_assignments on this same migrate's after_migrate hook.

Also drops the "Checked By" Property Setter (Crema Guardrail.guard_interface): that
field is gone, replaced by guard_provider/guard_model.
"""

from __future__ import annotations

import frappe
from crema import guardrails as _guardrails
from crema import install, interfaces

_SEVERITY = _guardrails.SEVERITY


def _most_severe(values: list[str]) -> str:
    return max(values, key=_SEVERITY.index) if values else "Off"


def execute() -> None:
    install.sync_guardrails()  # make sure the Single and its default rows exist

    columns = [
        col
        for col in ("enable_prompt_scan", "enable_llm_guard", "output_trap", "masking")
        if frappe.db.has_column("Crema Model Assignment", col)
    ]
    if not columns:
        return  # fresh install — keep the seeded defaults

    # The column list is a fixed literal filtered by has_column — no user input.
    rows = frappe.db.sql(  # nosemgrep: frappe-sql-format-injection
        f"select interface, {', '.join(columns)} from `tabCrema Model Assignment`"
        " where parent = 'Crema Settings'",
        as_dict=True,
    )
    known = set(interfaces.names())  # "security" is gone from names(); drop its row here
    rows = [row for row in rows if row.interface in known]
    if not rows:
        return

    def ladder(row, column: str) -> str:
        if column not in columns:
            return "Off"
        value = row.get(column)
        if column in ("enable_prompt_scan", "enable_llm_guard"):
            return "Block" if value else "Off"
        return value or "Off"

    per_guardrail = {
        "scan": "enable_prompt_scan",
        "llm_guard": "enable_llm_guard",
        "trap": "output_trap",
        "pi": "masking",
    }

    doc = frappe.get_single("Crema Guardrails")
    for grow in doc.guardrails:
        column = per_guardrail.get(grow.guardrail)
        if column is None:
            continue  # "phi" is new — stays at its seeded default (Off)
        actions = {row.interface: ladder(row, column) for row in rows}
        enabled = [name for name, action in actions.items() if action != "Off"]
        grow.action = _most_severe(list(actions.values()))
        grow.interfaces = "" if set(enabled) >= {row.interface for row in rows} else ", ".join(enabled)

    if "enable_llm_guard" in columns:
        old_row = frappe.db.sql(
            "select system_prompt, provider, model from `tabCrema Model Assignment`"
            " where parent = 'Crema Settings' and interface = 'security'",
            as_dict=True,
        )
        guard_row = next((r for r in doc.guardrails if r.guardrail == "llm_guard"), None)
        if guard_row is not None and old_row:
            old_row = old_row[0]
            if old_row.system_prompt:
                guard_row.guard_prompt = old_row.system_prompt
            if old_row.provider:
                guard_row.guard_provider = old_row.provider
            if old_row.model:
                guard_row.guard_model = old_row.model

    # The guard's save-time executor check would throw here if the old site had the
    # guard on but no resolvable provider — that is exactly the fail-loud
    # misconfiguration the old CremaSettings rule threw for, so let it throw.
    doc.save(ignore_permissions=True)

    frappe.db.delete("Property Setter", {"doc_type": "Crema Guardrail", "field_name": "guard_interface"})
