"""App install hooks."""

from __future__ import annotations

import re

import frappe
from frappe.utils import validate_email_address


def after_install() -> None:
    """Idempotently create the 'Crema User' role, then seed interfaces (which also
    seeds the default isolation user — see sync_interfaces) and the workspace's usage
    dashboard."""
    _ensure_crema_user_role()
    sync_interfaces()
    sync_dashboard()


def _ensure_crema_user_role() -> None:
    """desk_access = 1 so the role can actually open Desk: it now also gates the desk
    UI (crema.bundle.js) for users who aren't System Manager, and a role with no desk
    access could never reach it."""
    if frappe.db.exists("Role", "Crema User"):
        return
    role = frappe.new_doc("Role")
    role.role_name = "Crema User"
    role.desk_access = 1
    role.insert(ignore_permissions=True)


_DEFAULT_ISOLATION_USER_EMAIL_SUFFIX = "crema@"


def _isolation_user_email() -> str:
    """`crema@<site>` when the site name is a usable email domain, else the same name
    with every character Frappe's email pattern rejects folded to a hyphen and
    `.localhost` appended. A site created as `mysite` or `test_site` has no dot (and an
    underscore is not a legal domain character), so the plain form would fail
    User.validate and take the whole `bench install-app crema` down with it."""
    site = frappe.local.site
    email = f"{_DEFAULT_ISOLATION_USER_EMAIL_SUFFIX}{site}"
    if validate_email_address(email):
        return email
    label = re.sub(r"[^a-z0-9-]+", "-", site.lower()).strip("-") or "site"
    return f"{_DEFAULT_ISOLATION_USER_EMAIL_SUFFIX}{label}.localhost"


def _ensure_isolation_user() -> str:
    """Idempotently create a dedicated, fenced isolation user — `crema@<site>` — with
    only the 'Crema User' role and no password (send_welcome_email = 0 and no password
    set means it can never log in; it exists purely as a sandbox identity). This is
    what makes "Default Isolation User" pre-filled out of the box instead of a hard
    blocker: without it, every interface using default_provider would still fail
    CremaSettings._validate_provider_isolation_pairing until an admin picked one by
    hand. It stays subject to the same fencing CremaModelAssignment.validate_isolation_user
    enforces on any other isolation user — in particular, it must never be given
    System Manager."""
    email = _isolation_user_email()
    if frappe.db.exists("User", email):
        return email

    user = frappe.new_doc("User")
    user.email = email
    user.first_name = "Crema"
    user.send_welcome_email = 0
    user.enabled = 1
    user.insert(ignore_permissions=True)
    user.add_roles("Crema User")
    return email


def sync_interfaces() -> None:
    """Save the Crema Settings Single so CremaSettings.validate reconciles its
    `assignments` child table to exactly one row per interfaces.names() name, and
    point default_isolation_user at the seeded isolation user if nothing else has been
    set there yet (never overwrites an admin's own choice).

    A seeded row has no provider — the Model Assignments grid always shows the full
    set, unconfigured rows included, and an unset provider is already what
    client._load_from_db treats as "not configured" (the fallback chain in
    client._resolve is unaffected). Run on install and re-run on every migrate (see
    hooks.after_migrate) so a name added to PREDEFINED later, or one a newly installed
    app registers through crema_interfaces, appears without a manual step.
    """
    settings = frappe.get_single("Crema Settings")
    if not settings.default_isolation_user:
        settings.default_isolation_user = _ensure_isolation_user()
    settings.save(ignore_permissions=True)


_DASHBOARD_CARDS = [
    {"name": "Calls", "function": "Count", "filters_json": "[]"},
    {
        "name": "Spend",
        "function": "Sum",
        "aggregate_function_based_on": "cost_usd",
        "filters_json": "[]",
    },
    {
        "name": "Blocked",
        "function": "Count",
        "filters_json": '[["Crema Log","status","=","Blocked"]]',
    },
]

# A Number Card's name *is* its label (Number Card.autoname), and the workspace points at
# these by name, so dropping the "Crema " prefix is a rename of a live record — not a
# relabel. Without this pass an already-installed site keeps the old cards and renders
# three empty slots. Safe to leave in place: it is a no-op once every site has migrated.
_RENAMED_DASHBOARD_CARDS = {
    "Crema Calls": "Calls",
    "Crema Cost (USD)": "Spend",
    "Crema Blocked": "Blocked",
}


def sync_dashboard() -> None:
    """Idempotently create the three Number Cards the Crema workspace links to
    (crema/crema/workspace/crema/crema.json's number_cards). All-time totals — Number
    Card filters_json takes literal filter values, not a relative-date placeholder, so
    a rolling window belongs to the Crema Usage report's date filters, not here. A
    Number Card is a real DocType record — there is nothing to seed from a fixtures
    export here, so this mirrors sync_interfaces' pattern instead."""
    for old, new in _RENAMED_DASHBOARD_CARDS.items():
        if frappe.db.exists("Number Card", old) and not frappe.db.exists("Number Card", new):
            frappe.rename_doc("Number Card", old, new, force=True)
            frappe.db.set_value("Number Card", new, "label", new)

    for card in _DASHBOARD_CARDS:
        if frappe.db.exists("Number Card", card["name"]):
            continue
        doc = frappe.new_doc("Number Card")
        doc.update(
            {
                "label": card["name"],
                "document_type": "Crema Log",
                "type": "Document Type",
                "function": card["function"],
                "aggregate_function_based_on": card.get("aggregate_function_based_on"),
                "filters_json": card["filters_json"],
                "is_public": 1,
                "module": "Crema",
            }
        )
        doc.name = card["name"]
        doc.insert(ignore_permissions=True)
