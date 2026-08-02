# Copyright (c) 2026, Crema and contributors
# For license information, please see license.txt

"""One row of a stored plan's field map, rendered as a real grid instead of raw JSON.

Virtual (`is_virtual` on both this doctype and the parent's `plan_field_map` field), so
there is no table behind it and nothing is ever written: the rows are derived from
`Crema Automation Task.plan_json`, which stays the single source of truth. The db_*
methods raise so a stray save can never silently persist a copy — same shape as
frappe's own `User Session Display`.
"""

from frappe.model.document import Document


class CremaPlanField(Document):
    def db_insert(self, *args, **kwargs):
        raise NotImplementedError

    def load_from_db(self, *args, **kwargs):
        raise NotImplementedError

    def db_update(self, *args, **kwargs):
        raise NotImplementedError

    def delete(self, *args, **kwargs):
        raise NotImplementedError
