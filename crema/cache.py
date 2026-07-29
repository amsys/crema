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


def clear_provider(name: str) -> None:
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
