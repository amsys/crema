"""Crema Automation Task controller.

`validate` is where the *authoring-time* fences live. Two of them are load-bearing for
security, not merely helpful:

* `Update Source Records` is refused unless the source is a Document Query. Without a
  source doctype, `validate` would leave `target_doctype` blank, and a blank
  `target_doctype` makes `automation._validate_plan`'s doctype check a no-op — a
  self-written plan could then name any doctype the isolation user can reach, with no
  `allowed_names` set to fence it either. Both fences dissolve from one empty field.
* A `source_doctype` in the Crema module is refused, so a task can never read (or be
  triggered by) crema's own audit rows.

Everything else here is UX: filling the cron from a preset, dropping a stale plan when
the shape it was written for changes, and warning at save time about the permission
failure that would otherwise only appear at 3am in `last_error`.
"""

from __future__ import annotations

from datetime import datetime

from croniter import croniter

import frappe
from crema import client, interfaces, sandbox
from frappe.model.document import Document
from frappe.utils import cint, now_datetime, split_emails, validate_email_address

# A blank preset means "I wrote the cron myself" — deliberately no doctype-level default,
# so migrating an existing task never rewrites the schedule it already had.
_SCHEDULE_PRESETS = {
    "Every 15 minutes": "*/15 * * * *",
    "Hourly": "0 * * * *",
    "Daily 03:00": "0 3 * * *",
    "Weekly (Mon 03:00)": "0 3 * * 1",
}

_DEFAULT_SOURCE_LIMIT = 50
_MAX_SOURCE_LIMIT = 200

# A stored plan is written for one (source, target, action, instruction) shape. Change any
# of them and it must be re-planned rather than re-run against a shape it never saw.
_PLAN_INPUTS = ("instruction", "source_type", "source_doctype", "target_doctype", "action")


class CremaAutomationTask(Document):
    def validate(self) -> None:
        self._apply_schedule_preset()

        if self.trigger != "Document Event" and not croniter.is_valid(self.schedule or ""):
            frappe.throw(f"'{self.schedule}' is not a valid cron expression.")

        if self.interface not in interfaces.names():
            frappe.throw(f"'{self.interface}' is not a known crema interface.")

        self._validate_source()
        self._validate_action()
        self._validate_trigger()

        # is_new() first: on an insert has_value_changed() is True for every field, which
        # would discard a plan supplied at creation time.
        if not self.is_new() and any(self.has_value_changed(fieldname) for fieldname in _PLAN_INPUTS):
            self.plan_json = None

        self._warn_unreadable_source()

    def onload(self) -> None:
        """Next scheduled run, for the form's status panel. Computed here rather than
        through a whitelisted endpoint — croniter is server-side, the value is read-only,
        and the form already loads the document."""
        if self.trigger == "Document Event" or not croniter.is_valid(self.schedule or ""):
            return
        self.set_onload(
            "next_run", croniter(self.schedule, self.last_run or now_datetime()).get_next(datetime)
        )

    def on_update(self) -> None:
        self._clear_event_cache()

    def on_trash(self) -> None:
        self._clear_event_cache()

    # -- pieces -------------------------------------------------------------------

    @staticmethod
    def _clear_event_cache() -> None:
        from crema import cache

        cache.clear_event_tasks()

    def _apply_schedule_preset(self) -> None:
        if preset := _SCHEDULE_PRESETS.get(self.schedule_preset or ""):
            self.schedule = preset

    def _validate_source(self) -> None:
        if self.source_type != "Document Query":
            return

        if not self.source_doctype:
            frappe.throw("A Document Query source needs a Source DocType.")
        if frappe.get_meta(self.source_doctype).module == "Crema":
            frappe.throw(
                f"'{self.source_doctype}' is a Crema doctype — a task cannot read crema's own records."
            )

        self.source_limit = min(cint(self.source_limit) or _DEFAULT_SOURCE_LIMIT, _MAX_SOURCE_LIMIT)

        try:
            # A trial query is the cheapest way to reject a bad fieldname/operator/shape
            # now, with frappe's own error message, instead of at 3am in last_error.
            frappe.get_all(self.source_doctype, filters=self._parsed_filters(), limit=1)
        except frappe.ValidationError:
            raise
        except Exception as exc:
            frappe.throw(f"Source Filters are not usable on {self.source_doctype}: {exc}")

    def _parsed_filters(self) -> dict | list:
        raw = (self.source_filters or "").strip()
        if not raw:
            return []
        try:
            filters = frappe.parse_json(raw)
        except Exception as exc:
            frappe.throw(f"Source Filters is not valid JSON: {exc}")
        if not isinstance(filters, dict | list):
            frappe.throw("Source Filters must be a JSON list or object.")
        return filters

    def _validate_action(self) -> None:
        if self.action == "Update Source Records":
            # See the module docstring — this is the fence, not a convenience check.
            if self.source_type != "Document Query":
                frappe.throw(
                    "Update Source Records needs a Document Query source — "
                    "there is nothing to update otherwise."
                )
            self.target_doctype = self.source_doctype
        elif self.action == "Report Only":
            self.target_doctype = None
            for address in split_emails(self.notify_to or ""):
                validate_email_address(address, throw=True)
        elif not self.target_doctype:
            frappe.throw("Upsert Records needs a Target DocType.")

    def _validate_trigger(self) -> None:
        if self.trigger != "Document Event":
            return
        if not self.event:
            frappe.throw("A Document Event trigger needs an Event.")
        if self.source_type != "Document Query":
            frappe.throw(
                "A Document Event trigger needs a Document Query source — it runs on the record that changed."
            )

    def _warn_unreadable_source(self) -> None:
        """Warn — never block — when the interface's isolation user cannot read what this
        task is pointed at. The interface may legitimately be unconfigured while the task
        is being authored, so an unresolvable one is Crema Settings' problem, not this
        form's. Blocking here would also make an unrelated Crema Settings change able to
        lock a System Manager out of editing tasks."""
        doctype = self.source_doctype if self.source_type == "Document Query" else self.target_doctype
        if not doctype:
            return
        try:
            isolation_user = client._resolve(self.interface)["isolation_user"]
            with sandbox.isolation(isolation_user):
                readable = frappe.has_permission(doctype, "read")
        except Exception:
            return

        if not readable:
            frappe.msgprint(
                f"'{isolation_user}' cannot read {doctype}. This task will fail until that is fixed.",
                indicator="orange",
            )
