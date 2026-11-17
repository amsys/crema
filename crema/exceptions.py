"""crema exception types.

Kept in their own module (rather than api.py, where the plan first sketches them) so
client.py can raise CremaConfigError without a circular import against api.py, which
itself imports client. Both api.py and crema/__init__.py re-export these.
"""

from __future__ import annotations

import frappe


class CremaBlockedError(frappe.ValidationError):
    """Raised when a guardrail blocks a call (the Text Scan, the Reply Check, masking,
    the Reply Filter, or a second retry), when a document has more pages than the
    limit, or when automation._check_egress refuses a source fetch."""


class CremaConfigError(frappe.ValidationError):
    """Raised when an interface can't be resolved to a usable, enabled provider, when
    the kill switch is on, when the sandbox refuses an isolation user, or when a
    Guardrails row set to Block names a check that is not installed."""


class CremaBudgetError(frappe.ValidationError):
    """Raised when an interface's monthly budget, or the calling user's monthly
    budget, has already been spent."""
