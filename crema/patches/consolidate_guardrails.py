"""Move the four per-interface safety fields onto the Crema Guardrails list.

The fields (enable_prompt_scan, enable_llm_guard, output_trap, masking) are gone from
Crema Model Assignment; their orphaned columns are still in the table (frappe never
drops a removed column), so this reads them straight off `tabCrema Model Assignment`
— each guarded by has_column, since `masking` only ever existed on the experimental
branch and a fresh install has none of the four.

One Guardrails row cannot express per-interface ACTIONS, only a per-interface filter:
where the old rows disagreed on a ladder value (output_trap Block here, Log Only
there), the MOST SEVERE value wins and the filter is the union of every interface that
had the check on. The old security row's custom system_prompt, provider, and model are
not carried over: the next patch in patches.txt, drop_ai_guard, removes the AI Guard
config outright, so migrating those fields here would be wasted work. The security
assignment row itself is removed by CremaSettings._reconcile_assignments on this same
migrate's after_migrate hook.

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
        if column is None or column not in columns:
            # "phi" is new, and a guardrail whose own legacy column never existed on
            # this site has nothing to fold — keep the seeded default. Folding an
            # absent column as "Off" would clobber scan's seeded "Block" on any site
            # that carries only a partial set of orphaned columns.
            continue
        actions = {row.interface: ladder(row, column) for row in rows}
        enabled = [name for name, action in actions.items() if action != "Off"]
        grow.action = _most_severe(list(actions.values()))
        grow.interfaces = "" if set(enabled) >= {row.interface for row in rows} else ", ".join(enabled)

    # The guard's save-time executor check would throw here if the old site had the
    # guard on but no resolvable provider — that is exactly the fail-loud
    # misconfiguration the old CremaSettings rule threw for, so let it throw.
    doc.save(ignore_permissions=True)

    frappe.db.delete("Property Setter", {"doc_type": "Crema Guardrail", "field_name": "guard_interface"})
