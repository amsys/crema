"""Reset the 'view' interface's stored system prompt when it still holds a crema default.

CremaModelAssignment.validate only seeds system_prompt when it is empty, so an existing
install keeps whatever was seeded at install time forever. The output contract (the
"action" discriminator: view/create/edit/delete/none) moved out of the system prompt and
into the user turn crema.bundle.js's crema_view_prompt builds — a stored prompt still
naming the old one-shape "view specification" contract contradicts it.

Only a row whose prompt is byte-identical to a crema default is rewritten: an admin who
edited theirs keeps it, and keeps working, because the contract is no longer in there
for their edit to contradict. OLD_PROMPTS is a tuple, not a single constant —
interfaces.DEFAULT_PROMPTS["view"] has been revised before (page_length was added after
the first release), so a site can hold either string. Append to it on any future edit;
test_doctypes pins that the current default is not itself in this tuple.
"""

from __future__ import annotations

import frappe
from crema import interfaces

OLD_PROMPTS = (
    # Original release.
    "You turn a request about a list of ERP records into a view specification. NEVER "
    "invent fieldnames — only use ones given in the schema. Output ONLY JSON "
    '`{"view": "List"|"Report"|"Kanban", "filters": {fieldname: [operator, value]}, '
    '"group_by": [fieldname, aggregate_fieldname, "count"|"sum"|"avg"] or null, '
    '"order_by": "fieldname asc"|"fieldname desc" or null, "columns": [fieldname, ...], '
    '"reason": "..."}`.',
    # + page_length.
    "You turn a request about a list of ERP records into a view specification. NEVER "
    "invent fieldnames — only use ones given in the schema. Output ONLY JSON "
    '`{"view": "List"|"Report"|"Kanban", "filters": {fieldname: [operator, value]}, '
    '"group_by": [fieldname, aggregate_fieldname, "count"|"sum"|"avg"] or null, '
    '"order_by": "fieldname asc"|"fieldname desc" or null, "page_length": integer or null, '
    '"columns": [fieldname, ...], "reason": "..."}`.',
)


def execute() -> None:
    settings = frappe.get_doc("Crema Settings")
    row = next((r for r in settings.assignments if r.interface == "view"), None)
    if row is None or row.system_prompt not in OLD_PROMPTS:
        return

    row.system_prompt = interfaces.DEFAULT_PROMPTS["view"]
    # A plain save, not a raw UPDATE: two caches hold this string —
    # frappe.get_cached_doc on the Single (client._assignment_row) and the redis
    # "crema:iface:" resolved config (client._resolve_one). CremaSettings.on_update ->
    # cache.clear_interfaces clears both; a bare UPDATE would leave the old prompt live
    # in both caches until the next unrelated Crema Settings save.
    settings.save(ignore_permissions=True)
