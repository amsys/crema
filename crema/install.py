"""App install hooks."""

from __future__ import annotations

import re

import frappe
from crema import interfaces
from frappe.utils import validate_email_address


def after_install() -> None:
    """Idempotently create the 'Crema User' role, then seed interfaces (which also
    seeds the default isolation user — see sync_interfaces), the Crema Automation Task
    interface picker's options (sync_interface_options), and the workspace's usage
    dashboard."""
    _ensure_crema_user_role()
    sync_interfaces()
    sync_interface_options()
    sync_dashboard()
    sync_example_task()


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


def sync_interface_options() -> None:
    """The interface list is fixed at install/migrate time — it changes only when an
    app registers its own through crema_interfaces, or a name is added to PREDEFINED —
    so it belongs in the doctype's meta, not in a per-request fetch. Meta-backed
    options are also what makes the list-view filter on this field usable; frappe
    builds that control from meta. Re-run on every migrate (hooks.after_migrate), same
    as sync_interfaces. Safe to re-run with no guard of our own: PropertySetter
    autonames deterministically and its validate() deletes the prior row when is_new(),
    so each run replaces rather than duplicates, and it clears the doctype cache itself.
    This has teeth: frappe validates Select options server-side on every save, so a
    site with an existing task whose interface is "security" or "advanced_ocr" would
    find that task unsavable after this runs — verified to affect zero records on the
    developer's own site, but a fresh site should not assume the same.
    """
    frappe.make_property_setter(
        {
            "doctype": "Crema Automation Task",
            "fieldname": "interface",
            "property": "options",
            # Leading blank line is frappe's convention for a blank first Select option.
            "value": "\n" + "\n".join(interfaces.selectable()),
            "property_type": "Text",
        },
        is_system_generated=True,
    )


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


_EXAMPLE_TASK_NAME = "Example — invoice from email"
_EXAMPLE_TASK_SEEDED = "crema_example_task_seeded"

_EXAMPLE_TASK_INSTRUCTION = """Each record below is an email that arrived in an inbox, \
followed by the text of any file attached to it. For every attachment that is a supplier \
invoice, pull out the supplier name, the invoice number, the invoice date, the due date, \
the currency, the net amount, the tax amount and the total. Ignore attachments that are \
not invoices. Say which emails you skipped and why."""


def sync_example_task() -> None:
    """Seed one disabled Crema Automation Task showing the emailed-invoice recipe.

    Seeded once ever, tracked by a frappe default rather than by the record existing: an
    admin who deletes or renames it must not have it come back on the next migrate, and an
    admin who edits it must not have the edit overwritten.

    The recipe is one field now — the Incoming Email trigger — so what is left here is the
    instruction, and the one filter that trigger does not preset.

    Deliberately `No Changes`: an example that shipped enabled, or pointed at a Purchase
    Invoice, would either write records nobody asked for or assume ERPNext is installed.
    It reads, reports, and waits to be pointed somewhere.
    """
    if frappe.db.get_default(_EXAMPLE_TASK_SEEDED):
        return
    # Set first: a failure below (no Communication doctype, a validation change) must not
    # make every subsequent migrate try again and fail again.
    frappe.db.set_default(_EXAMPLE_TASK_SEEDED, "1")

    if frappe.db.exists("Crema Automation Task", _EXAMPLE_TASK_NAME):
        return

    doc = frappe.new_doc("Crema Automation Task")
    doc.update(
        {
            "task_name": _EXAMPLE_TASK_NAME,
            "enabled": 0,
            # The Incoming Email trigger fills in the event and the Communication source
            # row itself (CremaAutomationTask._apply_email_trigger).
            "trigger": "Incoming Email",
            "interface": "extraction",
            "instruction": _EXAMPLE_TASK_INSTRUCTION,
            "action": "No Changes",
        }
    )
    # _apply_email_trigger only seeds a Communication row when there is not one already,
    # so supplying it here is how the example adds the one filter the trigger does not
    # preset: an invoice arrives as a file, so messages carrying none are ignored.
    doc.append(
        "sources",
        {
            "source_type": "Document Query",
            "source_doctype": "Communication",
            "source_filters": frappe.as_json(
                [["sent_or_received", "=", "Received"], ["has_attachment", "=", 1]]
            ),
            "source_limit": 5,
            "incremental": 1,
            "read_attachments": 1,
        },
    )
    doc.insert(ignore_permissions=True)
