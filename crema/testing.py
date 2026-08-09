"""Supported bootstrap for a consuming app's own test suite — not a test module itself,
so frappe's test loader (which globs test_*.py) never picks it up.

A consuming app that hard-depends on crema (required_apps) needs client._resolve() to
succeed before its own mock of client._complete is ever reached, or every call in its
suite raises CremaConfigError first. crema's own suite has had this exact sequence in
test_fixtures._ensure_provider/_ensure_interface for a while — this module is that same
sequence, promoted to a public, documented name a downstream app can actually import.
See PLAN.md item 8 and docs/use.md's "Testing an app that uses crema".
"""

from __future__ import annotations

import frappe

# Also imported by crema/test_fixtures.py, so the fixture's provider/isolation-user
# names are defined in exactly one place.
DEFAULT_PROVIDER = "_Test Crema Provider"
DEFAULT_ISOLATION_USER = "_test_crema_isolation@example.com"


def seed_provider(
    name: str = DEFAULT_PROVIDER,
    *,
    base_url: str = "http://localhost:11434/v1",
    api_key: str | None = None,
    model: str = "test-model",
    isolation_user: str = DEFAULT_ISOLATION_USER,
    set_defaults: bool = True,
) -> str:
    """Make this site configured enough that client._resolve() stops raising
    CremaConfigError — client._complete is then the only thing a caller's test needs
    to mock.

    Idempotent and non-overriding: a `name` provider that already exists is left as
    is, and `set_defaults` only fills a blank Crema Settings default, never replaces
    one already set — calling this twice, or on a site an admin already configured by
    hand, changes nothing. Clears the disabled kill switch so _resolve does not raise
    CremaConfigError for that reason.

    `base_url`'s default is a private address, so CremaProvider.validate needs no
    `api_key` to enable it (see crema/security.md's provider-key rule). Does not
    commit — same as every other write in this call chain, that is the caller's job
    (see docs/use.md's "Testing an app that uses crema" for the before_tests shape).
    """
    if not frappe.db.exists("Crema Provider", name):
        provider = frappe.new_doc("Crema Provider")
        provider.provider_name = name
        provider.base_url = base_url
        provider.enabled = 1
        provider.timeout_seconds = 5
        if api_key:
            provider.api_key = api_key
        provider.insert(ignore_permissions=True)

    if set_defaults:
        if not frappe.db.exists("User", isolation_user):
            user = frappe.new_doc("User")
            user.email = isolation_user
            user.first_name = isolation_user.split("@")[0]
            user.send_welcome_email = 0
            user.enabled = 1
            user.insert(ignore_permissions=True)

        settings = frappe.get_single("Crema Settings")
        changed = False
        if not settings.default_provider:
            settings.default_provider = name
            changed = True
        if not settings.default_model:
            settings.default_model = model
            changed = True
        if not settings.default_isolation_user:
            settings.default_isolation_user = isolation_user
            changed = True
        if settings.get("disabled"):
            settings.disabled = 0
            changed = True
        if changed:
            settings.save(ignore_permissions=True)

    return name
