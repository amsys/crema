"""Redis cache key builders + invalidation for crema.

All keys live under the ``crema:`` prefix in ``frappe.cache`` (redis, prefixed with the
site's db name by frappe itself).
"""

from __future__ import annotations

import frappe


def interface_key(interface: str) -> str:
    """Key for a resolved interface config (see client._resolve)."""
    return f"crema:iface:{interface}"


def response_key(prompt_sha: str) -> str:
    """Key for a cached response, addressed by content hash — never stale."""
    return f"crema:resp:{prompt_sha}"


def event_tasks_key() -> str:
    """Key for the {(doctype, event): tasks} map automation.on_doc_event reads. That
    handler runs on EVERY document write on the site, so this must stay one cached read,
    never a query."""
    return "crema:event_tasks"


def clear_event_tasks() -> None:
    """A Crema Automation Task saved/deleted: drop the doc-event map so a newly enabled
    (or disabled) Document Event task takes effect on the next write."""
    frappe.cache.delete_value(event_tasks_key())
    frappe.local.crema_event_tasks = None


def clear_provider() -> None:
    """Provider saved/deleted: nuke every resolved interface config (few providers,
    nuke-all is fine — cheap to rebuild) and the provider's cached model list."""
    frappe.cache.delete_keys("crema:iface:")

    from crema.client import list_models

    list_models.clear_cache()


def clear_interfaces() -> None:
    """Crema Settings saved: nuke every resolved interface config. The Single holds
    all 11 assignment rows in one document, so a save invalidates all-or-nothing —
    same cheap "few, nuke-all" trade-off clear_provider already makes. frappe's own
    document cache for the Single itself (frappe.get_cached_doc) is cleared by
    Document.save independently of this."""
    frappe.cache.delete_keys("crema:iface:")


def health_words_key() -> str:
    """Key for this site's enabled Crema Health Word rows — the per-site extension to
    mask._PHI_TERMS read by guardrails._phi_patterns."""
    return "crema:health_words"


def clear_health_words() -> None:
    """Crema Health Word saved/deleted: drop the cached word list so the next request
    picks up the change."""
    frappe.cache.delete_value(health_words_key())
