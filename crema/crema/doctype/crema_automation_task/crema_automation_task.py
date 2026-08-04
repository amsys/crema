"""Crema Automation Task controller.

`validate` is where the *authoring-time* fences live. The load-bearing one for security is
that `Update the Records It Read` is refused unless the task has exactly one Document Query
source. Without a source doctype, `validate` would leave `target_doctype` blank, and a
blank `target_doctype` makes `automation._validate_plan`'s doctype check a no-op — a
self-written plan could then name any doctype the isolation user can reach, with no
`allowed_names` set to fence it either. Both fences dissolve from one empty field. Two
query sources are refused for the same reason from the other end: one plan writes back to
one doctype, and `allowed_names` is one flat set of record names.

The per-row fences (a Crema-module doctype is refused, filters must parse and run) live in
the child controller, `crema_automation_source.py`.

Everything else here is UX: filling the cron from a preset, dropping a stale plan when
the shape it was written for changes, and warning at save time about the permission
failure that would otherwise only appear at 3am in `last_error`.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from croniter import croniter

import frappe
from crema import client, interfaces, sandbox
from crema.crema.doctype.crema_model_assignment.crema_model_assignment import validate_isolation_user
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime, split_emails, validate_email_address

# A blank preset means "I wrote the cron myself" — deliberately no doctype-level default,
# so migrating an existing task never rewrites the schedule it already had.
_SCHEDULE_PRESETS = {
    "Every 15 minutes": "*/15 * * * *",
    "Hourly": "0 * * * *",
    "Daily 03:00": "0 3 * * *",
    "Weekly (Mon 03:00)": "0 3 * * 1",
}

# A stored plan is written for one (source, target, action, instruction) shape. Change any
# of them and it must be re-planned rather than re-run against a shape it never saw. The
# sources are compared separately — see _sources_signature.
_PLAN_INPUTS = ("instruction", "target_doctype", "action")

# What an Incoming Email task watches. Frappe writes one of these per received message,
# whichever inbox it arrived in, and attaches the message's files to it.
_EMAIL_DOCTYPE = "Communication"


def _sources_signature(doc) -> list[tuple[str, str]]:
    """What a plan was written against: which sources, in order, and what each points at.
    has_value_changed() is no use on a Table field — it compares lists of Documents and is
    always True — and a filter or limit edit deliberately must NOT throw the plan away:
    the content keeps the same shape, only less of it."""
    return [(row.source_type or "", row.source_doctype or row.source_url or "") for row in doc.sources]


class CremaAutomationTask(Document):
    def validate(self) -> None:
        self._apply_schedule_preset()

        if self.trigger == "Schedule" and not croniter.is_valid(self.schedule or ""):
            frappe.throw(f"'{self.schedule}' is not a valid cron expression.")

        if self.interface not in interfaces.names():
            frappe.throw(f"'{self.interface}' is not a known crema interface.")

        # Same fencing rules as a Crema Model Assignment row's own Runs As, reused rather
        # than restated: an override that could be Administrator or a System Manager would
        # dissolve the sandbox exactly as the per-interface one would.
        validate_isolation_user(self.run_as, _("Runs As"))

        # Before the child loop below: the row this may add has to be stamped and
        # trial-queried like any other.
        self._apply_email_trigger()

        # Frappe does NOT call a child row's own controller validate() as part of a parent
        # save — Document._validate() only runs generic field-level checks on children,
        # and run_method("validate") is invoked on the parent only. Same explicit call as
        # CremaSettings.validate makes for its assignments; without it the per-row fences
        # (and the two grid columns the rows stamp) silently never run.
        for source in self.sources:
            source.validate()

        self._validate_sources()
        self._validate_action()
        self._validate_trigger()

        if self._plan_shape_changed():
            self.plan_json = None

        self._warn_unreadable_sources()

    def query_sources(self) -> list:
        """The Document Query rows. The URL rows need no site-side fencing, so most of
        this file — and automation._read_source's doc_name narrowing — only cares about
        these."""
        return [row for row in self.sources if row.source_type == "Document Query"]

    def file_sources(self) -> list:
        """The File Query rows — at most one, enforced by _validate_sources below."""
        return [row for row in self.sources if row.source_type == "File Query"]

    def record_sources(self) -> list:
        """Every row that can drive a Document Event trigger — a Document Query on the
        changed doctype, or a File Query (which always watches "File")."""
        return self.query_sources() + self.file_sources()

    # -- virtual fields -----------------------------------------------------------
    #
    # Read-only views onto data the form used to render as a hand-built HTML blob. Each
    # one backs an `is_virtual` field in the doctype JSON, so frappe reads it through the
    # property and never creates a column or stores a row — `plan_json` stays the single
    # source of truth for `automation.py`.
    #
    # These run on every document load, including list views. A property that raises
    # would break the load itself, so all of them swallow bad input and return an empty
    # value instead: a stored plan is model-authored and an old one may not match the
    # shape the current code expects.

    @property
    def next_run(self) -> datetime | None:
        if not self.enabled:
            return None
        if self.trigger == "Once":
            # Same "is it still armed" test automation.tick() makes, and for the same
            # reason: last_run is stamped before the run, so run_at having overtaken it is
            # what says "not run yet" — and moving run_at forward is what re-arms it.
            if not self.run_at:
                return None
            run_at = get_datetime(self.run_at)
            return None if self.last_run and run_at <= get_datetime(self.last_run) else run_at
        if self.trigger != "Schedule":
            return None
        if not croniter.is_valid(self.schedule or ""):
            return None
        return croniter(self.schedule, self.last_run or now_datetime()).get_next(datetime)

    @property
    def webhook_endpoint(self) -> str:
        return f"/api/method/crema.api.trigger_automation?task={quote(self.name or '')}"

    def isolation_user(self, cfg: dict) -> str:
        """The account this task acts as: its own Runs As when set, otherwise the one the
        use case resolved to. One place, so the sandbox, the save-time readability warning
        and the incremental 'skip my own writes' filter can never disagree."""
        return self.run_as or cfg["isolation_user"]

    @property
    def plan_target_doctype(self) -> str | None:
        return self._plan_map().get("doctype")

    @property
    def plan_match_fields(self) -> str | None:
        fields = self._plan_map().get("match_fields")
        return ", ".join(fields) if isinstance(fields, list) else None

    @property
    def plan_field_map(self) -> list[dict[str, str]]:
        field_map = self._plan_map().get("field_map")
        if not isinstance(field_map, dict):
            return []
        return [{"source_key": str(k), "target_field": str(v)} for k, v in field_map.items()]

    @property
    def plan_prompt(self) -> str | None:
        extract = self._plan().get("extract")
        return extract.get("prompt") if isinstance(extract, dict) else None

    def _plan(self) -> dict:
        if not self.plan_json:
            return {}
        try:
            plan = frappe.parse_json(self.plan_json)
        except Exception:
            return {}
        return plan if isinstance(plan, dict) else {}

    def _plan_map(self) -> dict:
        plan_map = self._plan().get("map")
        return plan_map if isinstance(plan_map, dict) else {}

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

    def _plan_shape_changed(self) -> bool:
        # get_doc_before_save() first: on an insert has_value_changed() is True for every
        # field, which would discard a plan supplied at creation time.
        before = self.get_doc_before_save()
        if not before:
            return False
        if any(self.has_value_changed(fieldname) for fieldname in _PLAN_INPUTS):
            return True
        return _sources_signature(self) != _sources_signature(before)

    def _validate_sources(self) -> None:
        """A task reads something, unless a webhook hands it the something — and it only
        does that while Use Webhook Data is on, so with the box off a source row is required
        again. The field's `mandatory_depends_on` says the same thing to the form, but
        frappe evaluates that client-side only — this is the fence, that is the hint."""
        if not self.sources and not (self.trigger == "Webhook" and self.read_webhook_payload):
            frappe.throw(
                _(
                    "Add at least one source. Only a Webhook task that uses webhook data may "
                    "have none, because the system calling it sends what to read."
                )
            )

        # A File Query skips PLAN/EXTRACT entirely and calls api.extract() per file — see
        # automation._run_inside. Mixing it with text sources would mean half the run
        # produces records and half produces prose, with nothing to join them into one
        # pipeline; refusing the combination is simpler than inventing one.
        if self.file_sources() and len(self.sources) > 1:
            frappe.throw(
                _(
                    "A File Query source cannot share a task with any other source — it reads "
                    "files into records on its own."
                )
            )

    def _validate_action(self) -> None:
        # Any action may email its result, so the recipients are checked for all of them.
        for address in split_emails(self.notify_to or ""):
            validate_email_address(address, throw=True)

        if self.file_sources():
            if self.action != "Create or Update Records":
                frappe.throw(
                    _(
                        "A File Query source only supports Create or Update Records — it always "
                        "produces new-or-changed records, never a report or an update to the "
                        "records it read."
                    )
                )
            if not self.target_doctype:
                frappe.throw(_("Create or Update Records needs a Record Type to Write."))
            self._validate_match_on()
        elif self.action == "Update the Records It Read":
            # See the module docstring — this is the fence, not a convenience check.
            queries = self.query_sources()
            if len(queries) != 1:
                frappe.throw(
                    _(
                        "Update the Records It Read needs exactly one Document Query source — it "
                        "writes back to the records it read, and cannot do that for two record "
                        "types at once."
                    )
                )
            self.target_doctype = queries[0].source_doctype
            self.match_on = None
        elif self.action == "No Changes":
            self.target_doctype = None
            self.match_on = None
        elif not self.target_doctype:
            frappe.throw(_("Create or Update Records needs a Record Type to Write."))
        else:
            self.match_on = None

    def _validate_match_on(self) -> None:
        """Which field(s) identify "the same record" across runs — admin-entered, not
        model-authored: the doctype's own metadata already answers this deterministically
        (a unique field, or the field autoname keys on), so asking an LLM would add cost
        and a failure mode for a question that has a fixed answer. Validated the same way
        automation._validate_field_maps checks a plan's match_fields."""
        fields = [f.strip() for f in (self.match_on or "").split(",") if f.strip()]
        if not fields:
            frappe.throw(_("A File Query source needs Match Records On — see its description."))

        known = {df.fieldname for df in frappe.get_meta(self.target_doctype).fields} | {"name"}
        if unknown := [f for f in fields if f not in known]:
            frappe.throw(
                _("Match Records On has unknown field(s) on {0}: {1}").format(
                    self.target_doctype, ", ".join(unknown)
                )
            )
        self.match_on = ", ".join(fields)

    def _apply_email_trigger(self) -> None:
        """Incoming Email is sugar over Document Event, materialised into the ordinary
        model: it fills in the event and the source row a mail-watching task always needs,
        and everything downstream then sees a plain Document Query on Communication. The
        seeded row is a normal row — visible, editable, deletable — and switching the
        trigger away leaves it alone.

        On Update, not After Insert: frappe's inbound mail inserts the Communication and
        only then attaches the files to it (frappe/email/receive.py InboundMail.process),
        so a task triggered on the insert would find no attachments.
        """
        if self.trigger != "Incoming Email":
            return

        self.event = "On Update"
        if any(row.source_doctype == _EMAIL_DOCTYPE for row in self.query_sources()):
            return

        self.append(
            "sources",
            {
                "source_type": "Document Query",
                "source_doctype": _EMAIL_DOCTYPE,
                "source_filters": frappe.as_json([["sent_or_received", "=", "Received"]]),
                # Every attachment is a separate billed OCR call, so a mail task reads a
                # small batch by default.
                "source_limit": 5,
                "incremental": 1,
                "read_attachments": 1,
            },
        )

    def _validate_trigger(self) -> None:
        if self.trigger == "Once":
            # mandatory_depends_on says the same thing to the form; frappe evaluates that
            # client-side only, so this is the fence — same split as _validate_sources.
            if not self.run_at:
                frappe.throw(_("A Once trigger needs a Run At time."))
            return

        if self.trigger not in ("Document Event", "Incoming Email"):
            return
        if not self.event:
            frappe.throw(_("A Document Event trigger needs an Event."))
        if not self.record_sources():
            frappe.throw(
                _(
                    "A Document Event trigger needs a Document Query or File Query source — it runs "
                    "on the record (or file) that changed."
                )
            )

    def _warn_unreadable_sources(self) -> None:
        """Warn — never block — when the account this task runs as cannot read what this
        task is pointed at. The interface may legitimately be unconfigured while the task
        is being authored, so an unresolvable one is Crema Settings' problem, not this
        form's. Blocking here would also make an unrelated Crema Settings change able to
        lock a System Manager out of editing tasks."""
        doctypes = [row.source_doctype for row in self.record_sources()]
        if not doctypes:
            doctypes = [self.target_doctype] if self.target_doctype else []
        if not doctypes:
            return
        try:
            isolation_user = self.isolation_user(client._resolve(self.interface))
            with sandbox.isolation(isolation_user):
                unreadable = [dt for dt in doctypes if not frappe.has_permission(dt, "read")]
        except Exception:
            return

        for doctype in unreadable:
            frappe.msgprint(
                f"'{isolation_user}' cannot read {doctype}. This task will fail until that is fixed.",
                indicator="orange",
            )
