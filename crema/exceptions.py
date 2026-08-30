"""crema exception types.

Kept in their own module (rather than api.py, where the plan first sketches them) so
client.py can raise CremaConfigError without a circular import against api.py, which
itself imports client. Both api.py and crema/__init__.py re-export these.
"""

from __future__ import annotations

import frappe


class CremaBlockedError(frappe.ValidationError):
    """Raised when a prompt is blocked by a security layer, or a source fetch is
    refused by automation._check_egress."""


class CremaConfigError(frappe.ValidationError):
    """Raised when an interface can't be resolved to a usable, enabled provider."""


class CremaBudgetError(frappe.ValidationError):
    """Raised when an interface's monthly_budget_usd has already been spent."""
