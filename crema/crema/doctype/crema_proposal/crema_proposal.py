# Copyright (c) 2026, Crema and contributors
# For license information, please see license.txt

"""Crema Proposal controller.

A parked row from a `Propose Only` automation run — see automation._propose and
automation.apply_proposal. Written and updated only from python, with
ignore_permissions=True, the same way Crema Log is; there is nothing for validate() to
check here.
"""

from __future__ import annotations

from frappe.model.document import Document


class CremaProposal(Document):
    pass
