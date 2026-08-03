"""Isolation-user sandbox for crema's document access.

All data access inside `isolation()` (File reads, future automation upserts via plain
`doc.save()` — never `ignore_permissions`) is fenced by the isolation user's Role
Profile + User Permissions at the DB layer (`frappe.get_list`/`has_permission`). No
custom permission-tree cache is needed: `frappe.set_user` resets `role_permissions`
and frappe's own permission caching covers it. The fenced `frappe.get_list` surface
this exposes doubles as the future tool-use API (see ROADMAP.md).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import frappe


@contextmanager
def isolation(user: str) -> Iterator[None]:
    """Run the wrapped code as `user`, restoring the original session after.

    `frappe.set_user` also replaces `session.sid` with the username and empties
    `session.data`, plus clears `frappe.local.form_dict` (the "form_dict footgun").
    `sid`/`data` belong to the *browser session*, not the user being impersonated:
    left clobbered, `Session.update()` at request end writes an empty payload over
    the real sid's cache entry, and the next request sees a stale `last_updated` and
    deletes the session as expired — logging the browser out. Restore all three
    immediately after every `set_user` call, including the one that reverts back.
    """
    session = frappe.local.session
    old_user, old_sid, old_data = session.user, session.sid, session.data
    old_form = frappe.local.form_dict

    def _restore() -> None:
        frappe.local.session.sid = old_sid
        frappe.local.session.data = old_data
        frappe.local.form_dict = old_form

    # This *is* the isolation sandbox — switching identity is the whole point, and
    # frappe's own permission engine stays the fence. Audited; see the module docstring
    # and docs/security.md.
    try:
        frappe.set_user(user)  # nosemgrep: frappe-setuser
        _restore()
        yield
    finally:
        frappe.set_user(old_user)  # nosemgrep: frappe-setuser
        _restore()
