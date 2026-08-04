"""Crema Usage — calls/tokens/cost aggregated from Crema Log, grouped by use case,
AI service, model, user, or day. Built for ROADMAP.md's "Usage/cost dashboards ... built
on Crema Log" item.

`group_by` is user input that reaches a raw SQL GROUP BY clause — mapped through
_GROUP_BY_FIELD, a fixed whitelist, and rejected outright if it's not one of the known
keys. Never interpolate the raw filter value into SQL.

The filters themselves live in crema_usage.js, not in crema_usage.json — see the comment
at the top of that file.
"""

from __future__ import annotations

import frappe
from crema import interfaces
from frappe import _
from frappe.utils import flt

# Keys are the desk labels the picker shows. "Interface" is kept as a hidden alias so a
# filter a user saved before the relabel still resolves instead of throwing at them.
_GROUP_BY_FIELD = {
    "Use Case": "interface",
    "Interface": "interface",
    "AI Service": "provider",
    "Model": "model",
    "User": "user",
    "Day": "DATE(creation)",
}


def execute(filters: dict | None = None):
    filters = filters or {}
    group_by = filters.get("group_by") or "Use Case"
    if group_by not in _GROUP_BY_FIELD:
        frappe.throw(_("Invalid group_by '{0}'").format(group_by))
    group_field = _GROUP_BY_FIELD[group_by]

    where, values = _where(filters)
    # group_field is never user input: group_by is rejected above unless it is one of
    # _GROUP_BY_FIELD's known keys, and only the mapped value reaches the query.
    rows = frappe.db.sql(  # nosemgrep: frappe-sql-format-injection
        f"""
        SELECT
            {group_field} AS group_value,
            COUNT(*) AS calls,
            SUM(CASE WHEN status = 'Cached' THEN 1 ELSE 0 END) AS cached,
            SUM(CASE WHEN status = 'Blocked' THEN 1 ELSE 0 END) AS blocked,
            SUM(CASE WHEN status = 'Error' THEN 1 ELSE 0 END) AS errors,
            SUM(prompt_tokens) AS prompt_tokens,
            SUM(completion_tokens) AS completion_tokens,
            SUM(cost_usd) AS cost_usd,
            AVG(duration_ms) AS avg_duration_ms
        FROM `tabCrema Log`
        {where}
        GROUP BY {group_field}
        ORDER BY calls DESC
        """,
        values,
        as_dict=True,
    )

    # Grouping on `interface` groups on the stored key, which is right — but the key is
    # not what the desk calls it anywhere else, so relabel for display only.
    label = interfaces.LABELS.get if group_field == "interface" else None
    data = [
        {
            **row,
            "group_value": label(row.group_value, row.group_value) if label else row.group_value,
            "cost_usd": flt(row.cost_usd, 6),
            "avg_duration_ms": flt(row.avg_duration_ms, 0),
        }
        for row in rows
    ]
    chart = {
        "data": {
            "labels": [str(row["group_value"] or "") for row in data],
            "datasets": [{"name": "Calls", "values": [row["calls"] for row in data]}],
        },
        "type": "bar",
    }
    return _columns(group_by), data, None, chart


def _where(filters: dict) -> tuple[str, dict]:
    clauses = []
    values: dict = {}
    if filters.get("from_date"):
        clauses.append("creation >= %(from_date)s")
        values["from_date"] = filters["from_date"]
    if filters.get("to_date"):
        clauses.append("creation <= %(to_date)s")
        values["to_date"] = filters["to_date"]
    if filters.get("interface"):
        # The table shows labels ("Default"), so typing what you see has to work; the
        # internal key ("simple") still does too. Both are stored columns.
        clauses.append("(interface = %(interface)s or interface_label = %(interface)s)")
        values["interface"] = filters["interface"]
    if filters.get("provider"):
        clauses.append("provider = %(provider)s")
        values["provider"] = filters["provider"]
    if filters.get("status"):
        clauses.append("status = %(status)s")
        values["status"] = filters["status"]
    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), values


def _columns(group_by: str) -> list[dict]:
    return [
        {"label": _(group_by), "fieldname": "group_value", "fieldtype": "Data", "width": 160},
        {"label": _("Calls"), "fieldname": "calls", "fieldtype": "Int", "width": 80},
        {"label": _("Cached"), "fieldname": "cached", "fieldtype": "Int", "width": 80},
        {"label": _("Blocked"), "fieldname": "blocked", "fieldtype": "Int", "width": 80},
        {"label": _("Errors"), "fieldname": "errors", "fieldtype": "Int", "width": 80},
        {"label": _("Prompt Tokens"), "fieldname": "prompt_tokens", "fieldtype": "Int", "width": 110},
        {"label": _("Completion Tokens"), "fieldname": "completion_tokens", "fieldtype": "Int", "width": 130},
        {"label": _("Cost (USD)"), "fieldname": "cost_usd", "fieldtype": "Currency", "width": 100},
        {"label": _("Avg Duration (ms)"), "fieldname": "avg_duration_ms", "fieldtype": "Int", "width": 130},
    ]
