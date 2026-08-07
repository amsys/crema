"""Site-wide write restrictions: the doctypes a System Manager has flagged as never
writable by Crema, whatever an interface's own isolation user could otherwise reach.

Its own module, not automation.py or log.py, because both automation's plan validation
and the desk UI's write-shape gating (via extend_bootinfo) consume the same list --
neither side owns the other.
"""

from __future__ import annotations

import frappe


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
