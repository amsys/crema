"""Site-wide restrictions: the disabled kill switch and the blocked-doctype list.

Its own module, not automation.py or log.py, because both automation's plan validation
and the desk UI's write-shape gating (via extend_bootinfo) consume the same list --
neither side owns the other. The kill switch is here for the same reason: client._resolve
and automation's three entry points all check it, and neither owns policy.
"""

from __future__ import annotations

import frappe
from frappe.utils.data import cint


def disabled() -> bool:
    """True when the site-wide kill switch is set — either in site_config.json or on
    Crema Settings' Check field. Either one kills; clearing both restores service.

    site_config.json is checked first so a desk edit cannot undo a config-level kill.
    cint is required because a JSON "0" is a truthy string.
    """
    return bool(
        cint(frappe.conf.get("crema_disabled"))
        or cint(frappe.get_cached_doc("Crema Settings").get("disabled"))
    )


def blocked_doctypes() -> set[str]:
    """The site's own red-line doctypes, from Crema Settings' Blocked Doctypes table."""
    settings = frappe.get_cached_doc("Crema Settings")
    return {row.document_type for row in settings.blocked_doctypes if row.document_type}


def extend_bootinfo(bootinfo) -> None:
    """Publish the blocked-doctype set to the desk as frappe.boot.crema_blocked_doctypes,
    so crema.bundle.js can fence its own write shapes client-side. Crema Settings is
    System-Manager read-only, so this is the only way an ordinary Crema User ever sees
    the list at all."""
    bootinfo.crema_blocked_doctypes = sorted(blocked_doctypes())
