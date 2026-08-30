"""Scheduler automation: SOURCE -> (self-)PLAN -> EXTRACT -> WRITE, with one replan.

Security posture — the reason this module is longer than "just call the LLM":

* The LLM output NEVER becomes code. A plan is *parameters only* (an extract prompt,
  a response schema, and fieldname->fieldname maps) consumed by the fixed pipeline
  below. `_validate_plan` rejects any key it does not know about, so a plan containing
  e.g. "code" or "script" is refused before it is ever stored.
* Every doctype/fieldname string that came from the LLM is checked against
  `frappe.get_meta` before it reaches the database, both for the parent and for the
  child table.
* A task reads one or more sources — a `Crema Automation Source` child row each, either a
  URL or a document query, in any mix. Every field on those rows is entered by a System
  Manager. The LLM cannot supply a URL, a doctype or a filter, and cannot add a source.
  The rows are read in order and joined into one labelled text; from EXTRACT onwards the
  pipeline neither knows nor cares how many there were.
* The whole run — the source read included — happens inside
  `sandbox.isolation(<interface isolation user>)`, and every write is a plain
  `doc.save()`. No `ignore_permissions` anywhere, so the isolation user's roles and
  User Permissions are the hard fence on what a task can read *and* write.

The four actions differ only in what stage 4 does with the extracted rows:

* `Create or Update Records` — find-or-create in `target_doctype`.
* `Update the Records It Read` — update, never create, and only records whose `name` the
  source query actually returned (`allowed_names`). `name` is usable as a *match* field
  but is never written: `doc.update({"name": ...})` mutates `self.name`, and
  `BaseDocument.db_update` would then write this document's values onto whatever row
  the model named, with no permission check on that row.
* `Propose Only` — write nothing. Park one `Crema Proposal` row per extracted row (or
  file record), for a human to approve or discard — see `_propose` and `apply_proposal`
  below. Approving replays the row through the same `_upsert`/`_upsert_files` a
  `Create or Update Records` run would have used.
* `No Changes` — no plan at all, and no writes. The plan machinery exists to map LLM
  output safely onto database fields; with nothing written there is nothing to validate.

Emailing the result is orthogonal to all three: any task with `notify_to` set mails what
the run did (a count for the write actions, the answer itself for `No Changes`).

Document-query content is user-editable, so it is a live injection channel — layer 1
still sees it (it travels in the *prompt* half of `api.ask_json`, and `security.scan`
scans prompt and context joined). Records that trip the scan are dropped individually
rather than failing the batch, since one soft hyphen in one record would otherwise take
out the whole run — and five such runs auto-disable the task.
"""

from __future__ import annotations

import hashlib
import html
import ipaddress
import mimetypes
import re
import socket
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, NamedTuple
from urllib.parse import urljoin, urlparse

import requests
from croniter import croniter

import frappe
from crema import _ocr, api, client, guardrails, log, policy, sandbox, security
from crema import terms as _terms
from crema.exceptions import CremaBlockedError
from frappe import _
from frappe.model import data_fieldtypes
from frappe.utils import add_days, cint, flt, now_datetime, strip_html

_PLAN_CONTENT_CHARS = 8000
_EXTRACT_CONTENT_CHARS = 60_000
_MAX_FAILURES = 5
_LOG_RETENTION_DAYS = 30  # fallback if Crema Settings.log_retention_days is unset
_ERROR_MAX_CHARS = 2000
_RESULT_MAX_CHARS = 20_000
_FETCH_TIMEOUT = 60
_FETCH_MAX_REDIRECTS = 5
_SOURCE_LIMIT_DEFAULT = 50
# One OCR call per attachment, billed and logged like any other. A record with a dozen
# scans attached would otherwise quietly multiply the cost of one run by twelve.
_ATTACHMENT_MAX_PER_RECORD = 5
# Per record, across every one of that record's child tables combined -- one number
# bounds the payload however many Table fields a doctype has. A 500-line invoice would
# otherwise multiply one record's share of the content budget on its own.
_CHILD_MAX_ROWS_PER_RECORD = 20
_WEBHOOK_PAYLOAD_CHARS = 20_000
# ponytail: a flat cap, not paging — source_limit tops out at 200, so one run cannot
# approach this. Add paging if a future caller pushes past it.
_WRITTEN_MAX = 1000

_FILE_DOCTYPE = "File"
_FILE_QUERY_FIELDS = ["name", "modified", "file_name", "file_url"]

_EVENT_METHODS = {
    "After Insert": "after_insert",
    "On Update": "on_update",
    "On Submit": "on_submit",
}

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)

_PLAN_KEYS = {"version", "extract", "map"}
_PLAN_REQUIRED = {"extract", "map"}
_EXTRACT_KEYS = {"prompt", "response_schema"}
_MAP_KEYS = {"doctype", "match_fields", "field_map", "child_table"}
_MAP_REQUIRED = {"doctype", "match_fields", "field_map"}
_CHILD_KEYS = {"fieldname", "match_fields", "field_map"}

_PLAN_SHAPE = """{
  "version": 1,
  "extract": {"prompt": "<instructions for reading the source text>",
              "response_schema": {"type": "json_object"}},
  "map": {"doctype": "<target doctype>",
          "match_fields": ["<fieldname used to find an existing record>"],
          "field_map": {"<key in an extracted row>": "<fieldname on the doctype>"},
          "child_table": {"fieldname": "<table fieldname>",
                          "match_fields": ["<child fieldname>"],
                          "field_map": {"<row key>": "<child fieldname>"}}}
}"""

_PLAN_RULES = """You are configuring a FIXED three-stage pipeline: SOURCE (already read) -> EXTRACT -> WRITE.
You do not write code, you do not invent stages, you only fill in the parameters below.
Your EXTRACT prompt must make the model return {"rows": [ ... ]} — a flat list of row objects.
Each row is upserted into one record of the target doctype; when a child table is configured,
the same row also contributes one child row to that record.
Output ONLY JSON with exactly this shape (omit "child_table" if the target has no child table):"""

_UPDATE_SOURCE_RULE = """This task UPDATES the records it just read — it never creates any.
So "map.match_fields" must be exactly ["name"], "map.field_map" must map one of your row keys
onto "name", and your EXTRACT prompt must tell the model to copy each record's "name" through
verbatim from the source. Rows naming a record the source query did not return are discarded."""


class AutomationError(frappe.ValidationError):
    """A plan was structurally invalid, or a stage of the pipeline failed."""


# ---------------------------------------------------------------------------
# Scheduler entry points
# ---------------------------------------------------------------------------


def tick() -> None:
    """Enqueue every enabled task that is due. Runs every 15 minutes — that tick interval
    is the effective granularity floor for both `schedule` and `run_at`.

    A `Once` task needs no done-flag of its own: run_task stamps `last_run` and commits it
    *before* doing any work, so `run_at > last_run` is both the fire-once guard (a crash
    mid-run cannot re-fire it) and the re-arm mechanism (move run_at forward and it is due
    again).
    """
    if policy.disabled():
        return

    now = now_datetime()
    for task in frappe.get_all(
        "Crema Automation Task",
        filters={"enabled": 1, "trigger": ("in", ("Schedule", "Once"))},
        fields=["name", "trigger", "schedule", "run_at", "last_run", "creation"],
    ):
        if task.trigger == "Once":
            if task.run_at and task.run_at <= now and not (task.last_run and task.run_at <= task.last_run):
                enqueue_task(task.name)
            continue
        try:
            due_at = croniter(task.schedule, task.last_run or task.creation).get_next(datetime)
        except Exception:
            continue  # invalid cron on an existing row — validate() blocks new ones
        if due_at <= now:
            enqueue_task(task.name)


def cleanup_logs() -> None:
    """Daily: drop Crema Log rows older than Crema Settings.log_retention_days.

    frappe.db.delete is a raw DELETE and does not cascade the way frappe.delete_doc does,
    so the row's comments have to go too. That matters beyond tidiness: on a
    developer_mode site those comments hold the full prompt and response text
    (log._attach_transcript), and left behind they would outlive the row that justified
    them — and grow without limit.
    """
    retention_days = frappe.db.get_single_value("Crema Settings", "log_retention_days") or _LOG_RETENTION_DAYS
    cutoff = add_days(now_datetime(), -retention_days)
    stale = frappe.get_all("Crema Log", filters={"creation": ("<", cutoff)}, pluck="name")
    if not stale:
        return
    frappe.db.delete("Comment", {"reference_doctype": "Crema Log", "reference_name": ("in", stale)})
    frappe.db.delete("Crema Log", {"name": ("in", stale)})


def enqueue_task(
    task: str, doc_doctype: str | None = None, doc_name: str | None = None, payload: str | None = None
) -> str:
    """Queue one run of `task`. Deduplicated — a task already queued or running is not
    queued twice. An event-triggered run is deduplicated per *document*, so re-saving one
    record debounces while two different records both run. Returns the job id.

    `enqueue_after_commit` matters for the event path: the handler fires inside the
    transaction that is still writing the record (and, for an inbound email, has not yet
    saved its attachments), so a worker that started immediately would read a row that
    does not exist yet.
    """
    job_id = f"crema-task-{task}-{doc_name}" if doc_name else f"crema-task-{task}"
    job = frappe.enqueue(
        "crema.automation.run_task",
        task=task,
        doc_doctype=doc_doctype,
        doc_name=doc_name,
        payload=payload,
        queue="long",
        timeout=1800,
        job_id=job_id,
        deduplicate=True,
        enqueue_after_commit=True,
    )
    return getattr(job, "id", None) or job_id


def on_doc_event(doc, method: str) -> None:
    """Wildcard `doc_events` handler — this runs on EVERY document write on the site, so
    the fast path is a flag check plus one dict lookup and nothing else.

    The flag is the reentrancy guard: without it a task that writes to the doctype it
    watches re-triggers itself forever, and every Crema Log row written during a run
    fires this handler too.
    """
    if getattr(frappe.local, "crema_in_automation", False):
        return
    tasks = _event_tasks().get((doc.doctype, method), ())
    if not tasks or policy.disabled():
        return
    for task in tasks:
        enqueue_task(task, doc_doctype=doc.doctype, doc_name=doc.name)


def _event_tasks() -> dict[tuple[str, str], tuple[str, ...]]:
    """{(doctype, method): (task, ...)} for enabled Document Event tasks. Memoized on
    `frappe.local` so a request that writes many documents does one redis read, not one
    per write; invalidated by CremaAutomationTask.on_update/on_trash."""
    if (memo := getattr(frappe.local, "crema_event_tasks", None)) is not None:
        return memo

    from crema import cache

    try:
        rows = frappe.cache.get_value(cache.event_tasks_key(), _fetch_event_tasks)
    except Exception:
        # This handler sits on every document write on the site. It is never allowed to
        # be the reason a save fails — during a migrate that adds these very columns, the
        # query below raises on every write until the schema catches up.
        frappe.local.crema_event_tasks = {}
        return {}

    mapping: dict[tuple[str, str], tuple[str, ...]] = {}
    for row in rows or []:
        method = _EVENT_METHODS.get(row["event"])
        if not row["source_doctype"] or not method:
            continue
        key = (row["source_doctype"], method)
        mapping[key] = (*mapping.get(key, ()), row["name"])

    frappe.local.crema_event_tasks = mapping
    return mapping


def _fetch_event_tasks() -> list[dict]:
    """One (task, source_doctype, event) row per document-query source of an enabled event
    task — a task watching two doctypes is listed under both. Incoming Email is a Document
    Event trigger with the Communication source and On Update filled in for you (see
    CremaAutomationTask._apply_email_trigger), so it is picked up here the same way."""
    events = {
        task["name"]: task["event"]
        for task in frappe.get_all(
            "Crema Automation Task",
            filters={"enabled": 1, "trigger": ("in", ("Document Event", "Incoming Email"))},
            fields=["name", "event"],
        )
    }
    if not events:
        return []
    rows = frappe.get_all(
        "Crema Automation Source",
        filters={
            "parenttype": "Crema Automation Task",
            "parent": ("in", list(events)),
            "source_type": ("in", ("Document Query", "File Query")),
        },
        fields=["parent", "source_doctype"],
    )
    return [
        {"name": row["parent"], "source_doctype": row["source_doctype"], "event": events[row["parent"]]}
        for row in rows
    ]


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def run_task(
    task: str, doc_doctype: str | None = None, doc_name: str | None = None, payload: str | None = None
) -> str:
    """Run one automation task end to end. `doc_doctype`/`doc_name` are set by an event
    trigger and narrow the matching document-query source to that one record; any other
    source is still read in full. `payload` is set by a webhook trigger and is read as one
    more source. Returns the recorded status."""
    if policy.disabled():
        return "Skipped"

    doc = frappe.get_doc("Crema Automation Task", task)

    # Captured before the read, and used as the new watermark for any source that did not
    # fill its batch: a record changed *during* a long run must be read by the next one.
    started = now_datetime()

    # Built here, before anything can fail — every _record() call below passes it, so
    # last_written_json always describes *this* run, including a run that fails before
    # it writes anything. See PLAN.md's "Undo a run".
    written = _Written(doc.name)

    # Stamp last_run first and commit: a task that explodes must not hot-loop on tick().
    # last_run is the cron cursor only — the incremental watermark is per source, in each
    # child row's last_read, so a run may not move this one backwards.
    doc.db_set("last_run", started, update_modified=False)
    # nosemgrep: frappe-manual-commit — the cursor must survive a failing run, see above
    frappe.db.commit()

    try:
        cfg = client._resolve(doc.interface)
    except Exception as exc:
        # e.g. the interface's provider was disabled. Left uncaught this skipped
        # _record() entirely, so last_status stayed stale and the 5-failure
        # auto-disable never fired while tick() re-enqueued the task every 15 minutes.
        return _record(doc, "Failed", f"interface unresolvable — {_describe(exc)}", written=written)

    # A copy, not a mutation: _resolve's dict may be shared, and everything downstream —
    # the sandbox, _read_documents' "skip my own writes" filter, _ocr's file loading —
    # reads the isolation user from cfg, so the task's own Runs As has to land here.
    cfg = {**cfg, "isolation_user": doc.isolation_user(cfg)}

    # The guard must cover every write this run makes, including _record's own db_set.
    frappe.local.crema_in_automation = True
    try:
        with sandbox.isolation(cfg["isolation_user"]):
            status, error, plan, result, watermarks = _run_inside(
                doc, cfg, started, doc_doctype, doc_name, payload, written=written
            )

        # Outside the sandbox on purpose: db_set stamps modified_by with the session user,
        # and the task document's own audit trail belongs to the scheduler, not to the
        # interface's isolation user.
        if plan is not None:
            doc.db_set("plan_json", frappe.as_json(plan))
        if status != "Failed":
            # Only now: a run that failed must re-read the same records next time, and a
            # source that failed while its siblings succeeded is simply absent from here.
            for source_name, read_up_to in watermarks.items():
                frappe.db.set_value(
                    "Crema Automation Source", source_name, "last_read", read_up_to, update_modified=False
                )
        outcome = _record(doc, status, error, result, written=written)
    finally:
        frappe.local.crema_in_automation = False

    _notify(doc, status, result)
    return outcome


def _run_inside(
    doc,
    cfg: dict,
    started,
    doc_doctype: str | None,
    doc_name: str | None,
    payload: str | None = None,
    written: _Written | None = None,
) -> tuple[str, str, dict | None, str, dict[str, Any]]:
    """Everything that runs as the isolation user. Returns
    (status, error, plan_to_store, result, watermarks)."""
    try:
        content, allowed_names, note, watermarks, records = _read_source(
            doc, cfg, started, doc_doctype, doc_name, payload=payload
        )
    except Exception as exc:
        return "Failed", f"source read failed — {_describe(exc)}", None, "", {}

    if not content.strip() and not records:
        # The normal state of a quiet incremental task. Left to fall through it would
        # cost two LLM calls, record Failed, and auto-disable the task after five quiet
        # nights. The watermarks still propagate: a batch whose records were all dropped
        # by the scan must still advance, or the backlog behind it is never read.
        return "Success", "", None, note or "no new records", watermarks

    if doc.file_sources():
        # PLAN/EXTRACT don't run for a File Query — extract() already planned against
        # target_doctype's own metadata per file (see _read_files). match_on is
        # admin-entered, not model-authored, for the same reason.
        match_fields = [f.strip() for f in (doc.match_on or "").split(",") if f.strip()]
        mapping = {"doctype": doc.target_doctype, "match_fields": match_fields}
        try:
            if doc.action == "Propose Only":
                entries = [(r["_source_file"], r, r.get("confidence")) for r in records]
                result = _propose(doc, mapping, entries)
            else:
                result = _summary(_upsert_files(doc.target_doctype, records, match_fields, written=written))
        except Exception as exc:
            return "Failed", _describe(exc), None, "", {}
        return "Success", "", None, _join_note(result, note), watermarks

    if doc.action == "No Changes":
        try:
            report = api.ask(doc.interface, doc.instruction, context=content)
        except Exception as exc:
            return "Failed", _describe(exc), None, "", {}
        return "Success", "", None, _join_note(report[:_RESULT_MAX_CHARS], note), watermarks

    status, error, plan, result = _plan_and_execute(doc, content, allowed_names, written=written)
    return status, error, plan, _join_note(result, note), watermarks


def _join_note(result: str, note: str) -> str:
    return f"{result} ({note})" if result and note else result or note


def _plan_and_execute(
    doc, content: str, allowed_names: set[str] | None, written: _Written | None = None
) -> tuple[str, str, dict | None, str]:
    """Execute the stored plan (planning first if there is none); on any failure,
    replan exactly once with the failure text appended and try again.

    Returns (status, error, plan_to_store, result). `plan_to_store` is None when the
    stored plan is still the right one — a plan is only persisted once it has actually run.
    """
    stored = _stored_plan(doc)
    try:
        plan = stored if stored is not None else _make_plan(doc, content)
        result = _execute(doc, plan, content, allowed_names, written=written)
        return "Success", "", None if stored is not None else plan, result
    except Exception as exc:
        first_error = _describe(exc)

    try:
        plan = _make_plan(doc, content, failure=first_error)
        result = _execute(doc, plan, content, allowed_names, written=written)
        return "Replanned", "", plan, result
    except Exception as exc:
        return "Failed", f"{first_error} || replan: {_describe(exc)}", None, ""


def _record(doc, status: str, error: str, result: str = "", written: _Written | None = None) -> str:
    """Persist the outcome. Five consecutive failures disable the task.

    `last_written_json` is set on every call, including a Failed one — a run that
    half-wrote before failing is exactly when "Undo Last Run" matters, and a run that
    never got past `client._resolve` (see `run_task`) must clear out the previous run's
    list rather than leave it stale."""
    written_json = (written or _Written(doc.name)).as_json()
    if status == "Failed":
        failures = (doc.consecutive_failures or 0) + 1
        doc.db_set(
            {
                "last_status": "Failed",
                "last_error": error[:_ERROR_MAX_CHARS],
                "last_result": result[:_RESULT_MAX_CHARS] or None,
                "last_written_json": written_json,
                "consecutive_failures": failures,
            }
        )
        if failures >= _MAX_FAILURES:
            doc.db_set("enabled", 0)
            frappe.log_error(
                title=f"Crema automation task disabled: {doc.name}"[:140],
                message=f"{failures} consecutive failures. Last error:\n{error}",
            )
    else:
        doc.db_set(
            {
                "last_status": status,
                "last_error": None,
                "last_result": result[:_RESULT_MAX_CHARS] or None,
                "last_written_json": written_json,
                "consecutive_failures": 0,
            }
        )
    return status


def _notify(doc, status: str, result: str) -> None:
    """Email what the run did — the answer itself for a No Changes task, the record counts
    for one that writes. Reporting is independent of the action: a task that writes may
    still say so. Outside the sandbox on purpose: the recipient list is
    System-Manager-entered and sending mail is a system capability, not a document
    permission — the same reasoning the URL fetch used to carry."""
    if status == "Failed" or not doc.notify_to or not result:
        return
    try:
        frappe.sendmail(
            recipients=frappe.utils.split_emails(doc.notify_to),
            subject=f"Crema: {doc.task_name}",
            content=f"<pre>{frappe.utils.escape_html(result)}</pre>",
        )
    except Exception as exc:
        # The run itself succeeded; a mail failure must not turn it into a Failed row and
        # count toward the auto-disable.
        frappe.log_error(title=f"Crema automation notify failed: {doc.name}"[:140], message=_describe(exc))


_TAG_RE = re.compile(r"<[^>]+>")


def _plain(text: str) -> str:
    """Frappe writes its own error messages with markup — `frappe.bold` wraps <strong>, and
    `throw(as_list=True)` builds a <ul> — and those are exactly the messages an automation
    run hits (a bad Link value, a missing mandatory field). `last_error` and `last_result`
    are read-only text fields, so the tags reach the admin as literal tags.

    A tag becomes a space, not nothing: a <br> between two words must not weld them. Hence
    not frappe's own strip_html/strip_html_tags, both of which substitute the empty string.
    Stripping rather than rendering is deliberate — the string is partly model-influenced
    (a plan's fieldnames reach frappe's error messages), so it must never become markup.
    """
    return " ".join(html.unescape(_TAG_RE.sub(" ", text)).split())


def _describe(exc: Exception) -> str:
    return log.redact(_plain(f"{type(exc).__name__}: {exc}"))[:_ERROR_MAX_CHARS]


# ---------------------------------------------------------------------------
# Stage 1 — SOURCE (pure code, no LLM involved)
# ---------------------------------------------------------------------------


def _read_source(
    doc,
    cfg: dict,
    started,
    doc_doctype: str | None,
    doc_name: str | None,
    preview: bool = False,
    payload: str | None = None,
) -> tuple[str, set[str] | None, str, dict[str, Any], list[dict]]:
    """Read every source row of this task, as one labelled text. Returns
    (content, allowed_names, note, watermarks, records).

    `allowed_names` is None when no document query contributed, and otherwise the exact
    set of record names the queries returned — it is what fences `Update the Records It Read`
    (which validate() holds to a single query source, so the set is one query's).

    `watermarks` is {source row name: read up to}, applied by run_task only if the run as
    a whole succeeded. A source that raised is absent from it, so it is re-read next time.

    `records` is non-empty only for a File Query source: each file already reads as
    {"set", "child_set"} records via api.extract(), not text, so there is nothing to add
    to `content` for it — see _read_files and _run_inside's file-source branch.
    CremaAutomationTask._validate_sources refuses a File Query mixed with any other
    source, so `content` and `records` are never both populated at once.

    A webhook `payload` counts as one more source while the task's Use Webhook Data box is
    on: it gets its own share of the budget and its own labelled block, and layer 1 scans it
    exactly like any other content. With the box off it is dropped here, which is what keeps
    the budget split, the labelled block and the all-sources-failed rescue below in step.

    Each source gets an equal share of the content budget: without that, one chatty source
    truncates the rest away and the run silently ignores them.
    """
    if not doc.read_webhook_payload:
        payload = None

    budget = _EXTRACT_CONTENT_CHARS // max(len(doc.sources) + (1 if payload else 0), 1)
    blocks = [f"=== Source: webhook payload ===\n{payload[:budget]}"] if payload else []

    reads, failures, records = [], [], []
    for source in doc.sources:
        try:
            read = _read_one(source, doc, cfg, started, doc_doctype, doc_name, budget, preview)
        except Exception as exc:
            failures.append(f"{source.heading() or source.source_type} failed — {_describe(exc)}")
            if doc.on_source_error:
                raise
            continue
        reads.append((source, read))
        records.extend(read.records)

    if failures and len(failures) == len(doc.sources) and not payload:
        raise AutomationError("; ".join(failures))

    blocks += [f"=== Source: {source.heading()} ===\n{r.text}" for source, r in reads if r.text]
    name_sets = [r.names for _, r in reads if r.names is not None]
    allowed_names = set().union(*name_sets) if name_sets else None
    notes = [f"{source.heading()}: {r.note}" for source, r in reads if r.note]
    watermarks = {source.name: r.read_up_to for source, r in reads if r.read_up_to}

    return "\n\n".join(blocks), allowed_names, "; ".join(notes + failures), watermarks, records


class _SourceRead(NamedTuple):
    """One source row's read, before _read_source assembles every row's into one
    result. `records` is non-empty only for a File Query row (see _read_source's own
    docstring); `text`/`names` are empty/None for one, since a File Query source never
    mixes with any other (CremaAutomationTask._validate_sources)."""

    text: str
    names: set[str] | None
    note: str
    read_up_to: Any
    records: list[dict]


@dataclass
class _Written:
    """What one run's writers actually did, for `last_written_json` / `created_json` —
    see PLAN.md's "Undo a run" item. Threaded as an accumulator, not a return value,
    because `_plan_and_execute` may call `_execute` twice (a replan) and both attempts'
    writes belong in one list.

    `record` keeps a record in `created` only, even if a later `_upsert` call in the same
    run updates it — a replan that creates a record, then fails, then on retry finds and
    updates that same record must not ask undo to both delete it and flag it for review.
    """

    task: str
    created: list[list[str]] = field(default_factory=list)
    updated: list[list[str]] = field(default_factory=list)
    truncated: bool = False

    def record(self, doctype: str, name: str, *, created: bool) -> None:
        pair = [doctype, name]
        if pair in self.created:
            return
        target = self.created if created else self.updated
        if len(self.created) + len(self.updated) >= _WRITTEN_MAX:
            self.truncated = True
            return
        target.append(pair)

    def as_json(self) -> str:
        return frappe.as_json({"created": self.created, "updated": self.updated, "truncated": self.truncated})


def _stamp(doc, written: _Written | None) -> None:
    """Mark `doc` as written by this task, the same native provenance marker
    `data_import`/`auto_repeat` set — see `frappe.core.doctype.version.version.for_insert`
    and `Document.save_version`. Independent of undo: it shows up in the desk's own
    Document History panel for any doctype that tracks changes, whether or not this run's
    own list is ever used."""
    if written is None:
        return
    doc.flags.updater_reference = {
        "doctype": "Crema Automation Task",
        "docname": written.task,
        "label": _("via Crema"),
    }


def _read_one(
    source,
    doc,
    cfg: dict,
    started,
    doc_doctype: str | None,
    doc_name: str | None,
    budget: int,
    preview: bool,
) -> _SourceRead:
    """Dispatch one source row to its reader — File Query / URL / Document Query."""
    if source.source_type == "File Query":
        narrow = doc_name if doc_doctype == _FILE_DOCTYPE else None
        rows, note, read_up_to = _read_files(source, doc, cfg, started, narrow, preview)
        return _SourceRead("", None, note, read_up_to, rows)
    if source.source_type != "Document Query":
        return _SourceRead(_fetch(source.source_url)[:budget], None, "", None, [])
    # An event run reads the one record that changed — but only from the source that
    # watches its doctype; a second source is reference material and must still be
    # read in full.
    narrow = doc_name if source.source_doctype == doc_doctype else None
    text, names, note, read_up_to = _read_documents(source, cfg, started, narrow, budget, preview)
    return _SourceRead(text, names, note, read_up_to, [])


def _source_fields(doctype: str) -> list[str]:
    """The fields a document query reads. Deliberately explicit rather than `["*"]`:
    `frappe.model.get_permitted_fields` short-circuits permlevel filtering entirely for
    CORE_DOCTYPES (frappe/model/__init__.py), so `["*"]` on e.g. User would hand
    `api_key` and `reset_password_key` straight to a third-party provider. Skipping the
    `_comments`/`_assign` default fields is a bonus: they are free-text and the single
    best injection surface on a record.
    """
    meta = frappe.get_meta(doctype)
    fields = [
        df.fieldname
        for df in meta.fields
        if df.fieldtype in data_fieldtypes and df.fieldtype != "Password" and not df.get("permlevel")
    ]
    return ["name", "modified", *fields]


def _child_fields(child_doctype: str) -> list[str]:
    """The fields one child row of a Document Query source reads -- _source_fields'
    fence (data_fieldtypes, no Password, no permlevel) minus `name`/`modified`, which
    are per-row noise a child row's own identity and edit time add nothing over the
    parent record's."""
    return [f for f in _source_fields(child_doctype) if f not in ("name", "modified")]


def _normalized_filters(raw: Any) -> list[list]:
    """One filter shape downstream. frappe.ui.FilterGroup writes a list of
    [fieldname, operator, value] (or 4-element, doctype-prefixed) rows; a hand-written
    dict is also accepted. Normalizing to the list form means an added filter ANDs with a
    user-supplied one on the same field instead of clobbering it."""
    if isinstance(raw, dict):
        return [[fieldname, "=", value] for fieldname, value in raw.items()]
    return [list(row) for row in raw or [] if isinstance(row, list | tuple)]


def _query_rows(
    doctype: str, source, cfg: dict, started, doc_name: str | None, fields: list[str], preview: bool
) -> tuple[list, Any]:
    """Filters + the incremental watermark + the get_list read fence — shared by
    _read_documents (Document Query) and _read_files (File Query).

    Returns (rows, read_up_to). `read_up_to` is None when the source isn't incremental.
    """
    filters = _normalized_filters(frappe.parse_json(source.source_filters or "[]"))

    # An event run reads one named record, so it is not a window over time and must never
    # move this source's watermark. A preview (Dry Run) ignores the watermark in both
    # directions: run right after a real run it would otherwise read nothing and look
    # broken, and it must not consume the window the next real run needs.
    incremental = bool(source.incremental) and not doc_name and not preview

    if doc_name:
        filters.append(["name", "=", doc_name])
    elif incremental and source.last_read:
        filters.append(["modified", ">", source.last_read])
        # This task's own writes bump `modified`. Without this an update-source task
        # task re-reads — and re-bills for — everything it just wrote, every tick,
        # forever. A later human edit sets modified_by back to the human, so the record
        # legitimately comes back.
        filters.append(["modified_by", "!=", cfg["isolation_user"]])

    # get_list, never get_all: this is the read fence. Oldest first, so a batch capped by
    # source_limit takes the head of the backlog and leaves the watermark at the last row
    # handled — newest-first would starve the tail permanently.
    limit = cint(source.source_limit) or _SOURCE_LIMIT_DEFAULT
    rows = frappe.get_list(doctype, filters=filters, fields=fields, limit=limit, order_by="modified asc")

    # A capped batch read the head of the backlog only, so the next run resumes at the
    # last row it actually handled; an uncapped one read everything up to the moment this
    # run started. `started`, not `now`: a record changed during a long run is not lost.
    read_up_to = None
    if incremental:
        read_up_to = rows[-1].get("modified") if len(rows) == limit else started
    return rows, read_up_to


def _read_documents(
    source, cfg: dict, started, doc_name: str | None, budget: int, preview: bool = False
) -> tuple[str, set[str], str, Any]:
    rows, read_up_to = _query_rows(
        source.source_doctype, source, cfg, started, doc_name, _source_fields(source.source_doctype), preview
    )
    if not rows:
        return "", set(), "", read_up_to

    notes = []
    if source.read_children:
        # Before the scan triage below, not after: a child row's own text is a better
        # injection surface than the parent's, and it must be scanned along with it --
        # not sail through unscanned because it arrived one line later.
        child_note = _attach_children(source.source_doctype, rows)
        if child_note:
            notes.append(child_note)

    kept, dropped = rows, 0
    interface = cfg.get("requested") or cfg.get("interface") or ""
    if guardrails.active("scan", interface) != "Off":
        # Triage, not the fence — api.ask_json's input gate still scans the assembled
        # content. Without it one soft hyphen in one record blocks all 50, and five
        # such runs disable the task. Only meaningful when the text scan actually
        # applies to this task's interface.
        extra = guardrails.scan_patterns()
        kept = [row for row in rows if not security.scan(frappe.as_json(row), extra=extra)]
        dropped = len(rows) - len(kept)

    if dropped:
        notes.append(f"{dropped} skipped by the security scan")
    if not kept:
        return "", set(), "; ".join(notes), read_up_to

    # Masking's term source (see crema.terms / crema.mask): harvested here, inside the
    # caller's sandbox.isolation, and appended rather than replaced — a task with
    # several Document Query sources calls _read_documents once per source, and each
    # source's terms are as real as the last one's. Skipped when no Hide Personal
    # Information row applies to this task's interface — the harvest is a
    # get_doc-per-row cost not worth paying for a masking pass that will never run
    # (guardrails.run drains the accumulator unconditionally either way).
    if guardrails.active("pi", interface) != "Off":
        term_groups = [g for row in kept for g in _terms.harvest(source.source_doctype, row["name"])]
        if term_groups:
            frappe.local.crema_terms = (getattr(frappe.local, "crema_terms", None) or []) + term_groups

    text = frappe.as_json(kept)
    if source.read_attachments:
        attachments, attachment_note = _read_attachments(source.source_doctype, kept)
        text = f"{text}\n{attachments}" if attachments else text
        if attachment_note:
            notes.append(attachment_note)

    return text[:budget], {row["name"] for row in kept}, "; ".join(notes), read_up_to


def _ocr_readable(file_name: str | None) -> bool:
    """_ocr.prep_parts returns [] for anything that is not a PDF or an image, and ocr()
    would still bill a provider call for the empty result — shared by _read_attachments
    and _read_files so a non-document file is skipped before that call, not after."""
    mime = mimetypes.guess_type(file_name or "")[0] or ""
    return mime == "application/pdf" or mime.startswith("image/")


def _read_files(
    source, doc, cfg: dict, started, doc_name: str | None, preview: bool
) -> tuple[list[dict], str, Any]:
    """The File Query reader. Reuses _query_rows' filter/watermark/get_list machinery over
    the File doctype, then calls api.extract() per file instead of serialising the row —
    PLAN and EXTRACT drop out of the pipeline for a File Query entirely, since extract()
    plans against the target doctype's own metadata (see the module docstring's action
    table and CremaAutomationTask._validate_action).

    Returns (records, note, read_up_to). A file that fails to extract — including a
    CremaBlockedError from layer 1 on poisoned document text, same as _read_documents'
    per-record scan drop — is skipped and noted rather than failing the whole source: one
    bad PDF must not disable the task after five runs.

    `doc.confidence_floor` gates here, before any writer sees a record: api.extract()'s
    confidence is the OCR pass's own measurement, and it is a per-file number — every
    record a file describes shares it, so a file below the floor is dropped whole rather
    than record by record. There is no equivalent floor for a plan-based extraction
    (_extract): that path has no measured confidence, only the model's own output, and
    gating on a self-reported number would not be a confidence check at all.
    """
    rows, read_up_to = _query_rows(_FILE_DOCTYPE, source, cfg, started, doc_name, _FILE_QUERY_FIELDS, preview)
    if not rows:
        return [], "", read_up_to

    floor = flt(doc.confidence_floor)
    records, failures, unreadable, low_confidence = [], [], 0, 0
    for row in rows:
        if not _ocr_readable(row["file_name"]):
            unreadable += 1
            continue
        try:
            result = api.extract(doc.target_doctype, row["file_url"])
        except Exception as exc:
            failures.append(f"{row['file_name']} — {_describe(exc)}")
            continue
        if not result["records"]:
            failures.append(f"{row['file_name']} — {result['reason'] or 'no records found'}")
            continue
        if result["confidence"] < floor:
            low_confidence += 1
            continue
        for record in result["records"]:
            record["confidence"] = result["confidence"]
            record["_source_file"] = row["name"]
        records.extend(result["records"])

    notes = []
    if unreadable:
        notes.append(f"{unreadable} file(s) skipped (not a PDF or image)")
    if low_confidence:
        notes.append(f"{low_confidence} file(s) skipped (confidence below {floor})")
    if failures:
        notes.append(f"{len(failures)} file(s) unreadable: {'; '.join(failures)}")
    return records, "; ".join(notes), read_up_to


def _attach_children(doctype: str, rows: list) -> str:
    """Merge each Table/Table MultiSelect field's rows onto the matching parent row
    dict, under the table's own fieldname -- so a Sales Invoice reaches the model with
    its item lines, a BOM with its components, where today it travels with none
    (`_source_fields` keeps to `data_fieldtypes`, which excludes both). Mutates `rows`
    in place.

    One frappe.get_list per child table for the WHOLE batch, not one per record --
    `parent_doctype` is what keeps this get_list (never get_all) fenced by frappe's
    own child-table permission check (`has_child_permission` resolves it through
    `parent_doctype`), the same argument `_read_attachments` already makes for File.

    `_CHILD_MAX_ROWS_PER_RECORD` is a per-record budget shared across every table, in
    field order: an earlier table drains it first, a later one gets whatever is left.
    The query's own `limit` only bounds the whole batch, not one record's share, so the
    per-record cap is enforced here, after the read. Returns a note naming how many
    records had at least one line left out, in the shape `_read_attachments` returns.
    """
    meta = frappe.get_meta(doctype)
    tables = [df for df in meta.fields if df.fieldtype in ("Table", "Table MultiSelect") and df.options]
    if not tables:
        return ""

    by_name = {row["name"]: row for row in rows}
    names = list(by_name)
    remaining = dict.fromkeys(names, _CHILD_MAX_ROWS_PER_RECORD)
    truncated: set[str] = set()

    for df in tables:
        child_rows = frappe.get_list(
            df.options,
            parent_doctype=doctype,
            filters={"parent": ["in", names], "parenttype": doctype, "parentfield": df.fieldname},
            fields=[*_child_fields(df.options), "parent"],
            order_by="parent asc, idx asc",
            limit=len(names) * _CHILD_MAX_ROWS_PER_RECORD,
        )
        grouped: dict[str, list[dict]] = {}
        for child in child_rows:
            grouped.setdefault(child.pop("parent"), []).append(child)

        for name, group in grouped.items():
            budget = remaining.get(name, 0)
            if len(group) > budget:
                truncated.add(name)
            kept = group[:budget]
            remaining[name] = budget - len(kept)
            if kept:
                by_name[name][df.fieldname] = kept

    return f"{len(truncated)} record(s) had table lines left out" if truncated else ""


def _read_attachments(doctype: str, rows: list) -> tuple[str, str]:
    """OCR the files attached to each record and return them as labelled text blocks.

    Attachments are a universal frappe concept — the sidebar, a drag-drop and an inbound
    email all produce the same File rows — so this is not an email feature. It is what
    makes "an invoice arrives by mail, read it into a record" reachable without code: an
    Email Account writes one Communication per message and attaches its files to it.

    frappe.get_list, not frappe.desk.form.load.get_attachments: that helper uses get_all,
    which ignores permissions, and everything inside sandbox.isolation goes through the
    permission engine. api.ocr -> _ocr._load_bytes now also checks the File document
    itself as the calling (isolation) user, so a File attached to a record this listing
    could see is checked twice, not zero times.
    """
    blocks, failures = [], []
    for row in rows:
        files = frappe.get_list(
            "File",
            filters={"attached_to_doctype": doctype, "attached_to_name": row["name"]},
            fields=["file_name", "file_url"],
            order_by="creation asc",
            limit=_ATTACHMENT_MAX_PER_RECORD,
        )
        for file in files:
            if not _ocr_readable(file.file_name):
                continue
            try:
                text = api.ocr(file.file_url)["text"]
            except Exception as exc:
                failures.append(f"{file.file_name} — {_describe(exc)}")
                continue
            if text:
                blocks.append(f"-- attachment {file.file_name} on {row['name']} --\n{text}")

    note = f"{len(failures)} attachment(s) unreadable: {'; '.join(failures)}" if failures else ""
    return "\n\n".join(blocks), note


def _check_egress(url: str) -> None:
    """Refuse a URL fetch aimed at anything but the open internet: cloud metadata
    endpoints (169.254.169.254 and its IPv6 twin), loopback, and every other private or
    reserved range. `_fetch` calls this before every hop of a redirect chain, not only
    the first, since a public URL is free to redirect to a private one.

    Resolves the host itself rather than delegating to requests: a private/loopback
    check on the URL string alone would miss a hostname that merely resolves there.
    A host that fails to resolve at all is let through -- there is nothing to connect
    to, so requests.get fails on its own a moment later, and this keeps the test
    suite's unresolvable *.invalid fixtures working unchanged. A DNS answer that
    changes between this check and requests' own resolution (rebinding) is a residual
    gap -- see docs/security.md."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise CremaBlockedError(f"source fetch: unsupported scheme in '{url}'")
    host = parsed.hostname
    if not host:
        raise CremaBlockedError(f"source fetch: no host in '{url}'")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror:
        return
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise CremaBlockedError(f"source fetch: '{host}' resolves to a non-public address")


def _fetch(url: str) -> str:
    """Fetch `url` and reduce it to plain text. PDFs go through the crema._ocr pipeline
    (embedded text when present, vision OCR when scanned), HTML is stripped, anything
    else is used as-is.

    Redirects are followed manually, one hop at a time, with _check_egress run before
    each one -- a public URL that 302s to a private address must be refused on the
    second hop, not just the first."""
    for _hop in range(_FETCH_MAX_REDIRECTS + 1):
        _check_egress(url)
        response = requests.get(url, timeout=_FETCH_TIMEOUT, allow_redirects=False)
        if not response.is_redirect:
            break
        url = urljoin(url, response.headers.get("location") or "")
    else:
        raise CremaBlockedError(f"source fetch: too many redirects starting at '{url}'")
    response.raise_for_status()

    body = response.content
    mime = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not mime:
        mime = _ocr._sniff_mime(body)

    if _ocr._is_pdf(body, mime):
        text = _ocr._extract_pdf_text(body)
        return text if len(text) >= _ocr._TEXT_PDF_MIN_CHARS else api.ocr(body)["text"]

    text = body.decode(response.encoding or "utf-8", errors="replace")
    if "html" in mime or "<html" in text[:1000].lower():
        return strip_html(_SCRIPT_STYLE_RE.sub(" ", text))
    return text


# ---------------------------------------------------------------------------
# Stage 2 — PLAN (LLM writes parameters; code validates every one of them)
# ---------------------------------------------------------------------------


def _stored_plan(doc) -> dict | None:
    """The saved plan, re-validated on load. An unparseable or now-invalid plan (the
    target doctype changed under it, say) is treated as absent so the task replans."""
    if not doc.plan_json:
        return None
    try:
        return _validate_plan(frappe.parse_json(doc.plan_json), doc.target_doctype)
    except Exception:
        return None


def _make_plan(doc, content: str, failure: str | None = None) -> dict:
    raw = api.ask_json(doc.interface, _planning_prompt(doc, content, failure))
    return _validate_plan(raw, doc.target_doctype)


def _plan_rules(doc) -> str:
    if doc.action == "Update the Records It Read":
        return f"{_PLAN_RULES}\n{_UPDATE_SOURCE_RULE}"
    return _PLAN_RULES


def _planning_prompt(doc, content: str, failure: str | None) -> str:
    parts = [_plan_rules(doc), _PLAN_SHAPE, f"Task instruction:\n{doc.instruction}"]
    if doc.target_doctype:
        parts.append(api._meta_summary(doc.target_doctype))
    parts.append(f"Source content (first {_PLAN_CONTENT_CHARS} characters):\n{content[:_PLAN_CONTENT_CHARS]}")
    if failure:
        parts.append(f"The previous attempt FAILED with:\n{failure}\nProduce a corrected plan.")
    return "\n\n".join(parts)


def _require_keys(obj: Any, allowed: set[str], required: set[str], where: str) -> None:
    if not isinstance(obj, dict):
        raise AutomationError(f"{where} must be an object")
    if unknown := set(obj) - allowed:
        raise AutomationError(f"{where} has unknown keys: {sorted(unknown)}")
    if missing := required - set(obj):
        raise AutomationError(f"{where} is missing keys: {sorted(missing)}")


def _validate_field_maps(match_fields: Any, field_map: Any, meta, where: str) -> None:
    """Every fieldname the LLM produced must exist on `meta`, and every match field must
    actually be populated by the field map (otherwise no filter can ever be built)."""
    if not isinstance(field_map, dict) or not field_map:
        raise AutomationError(f"{where}.field_map must be a non-empty object")
    if not isinstance(match_fields, list) or not match_fields:
        raise AutomationError(f"{where}.match_fields must be a non-empty list")

    # `name` is not a docfield, but it is the only stable identifier an update-source plan
    # can match on. Allowing it here is safe *only* because _upsert never writes it — see
    # that function and the module docstring.
    known = {df.fieldname for df in meta.fields} | {"name"}
    for key, fieldname in field_map.items():
        if not isinstance(key, str) or not isinstance(fieldname, str):
            raise AutomationError(f"{where}.field_map must map strings to strings")
        if fieldname not in known:
            raise AutomationError(f"{where}.field_map targets unknown field '{fieldname}'")

    mapped = set(field_map.values())
    for fieldname in match_fields:
        if fieldname not in known:
            raise AutomationError(f"{where}.match_fields has unknown field '{fieldname}'")
        if fieldname not in mapped:
            raise AutomationError(f"{where}.match_fields field '{fieldname}' is not in field_map")


def _validate_plan(plan: Any, target_doctype: str | None) -> dict:
    """Structural validation of an LLM-authored plan. Unknown keys are rejected outright,
    which is what keeps executable content ("code", "script", ...) out of a plan."""
    _require_keys(plan, _PLAN_KEYS, _PLAN_REQUIRED, "plan")

    extract = plan["extract"]
    _require_keys(extract, _EXTRACT_KEYS, {"prompt"}, "plan.extract")
    if not isinstance(extract["prompt"], str) or not extract["prompt"].strip():
        raise AutomationError("plan.extract.prompt must be a non-empty string")
    if "response_schema" in extract and not isinstance(extract["response_schema"], dict):
        raise AutomationError("plan.extract.response_schema must be an object")

    mapping = plan["map"]
    _require_keys(mapping, _MAP_KEYS, _MAP_REQUIRED, "plan.map")

    doctype = mapping["doctype"]
    if not isinstance(doctype, str) or not frappe.db.exists("DocType", doctype):
        raise AutomationError(f"plan.map.doctype '{doctype}' does not exist")
    # Refuse rather than skip the check on a blank target: with no target to compare
    # against, a self-written plan could name any doctype the isolation user can reach.
    if not target_doctype:
        raise AutomationError("the task has no target doctype, so no plan can be validated against it")
    if doctype != target_doctype:
        raise AutomationError(f"plan.map.doctype '{doctype}' is not the task's target '{target_doctype}'")

    blocked = policy.blocked_doctypes()
    if doctype in blocked:
        raise AutomationError(f"'{doctype}' is on this site's blocked doctype list — see Crema Settings.")

    meta = frappe.get_meta(doctype)
    _validate_field_maps(mapping["match_fields"], mapping["field_map"], meta, "plan.map")

    child = mapping.get("child_table")
    if child is not None:
        _require_keys(child, _CHILD_KEYS, _CHILD_KEYS, "plan.map.child_table")
        child_df = meta.get_field(child["fieldname"])
        if not child_df or child_df.fieldtype != "Table":
            raise AutomationError(f"plan.map.child_table.fieldname '{child['fieldname']}' is not a table")
        if child_df.options in blocked:
            raise AutomationError(
                f"'{child_df.options}' (plan.map.child_table) is on this site's blocked doctype list "
                "— see Crema Settings."
            )
        _validate_field_maps(
            child["match_fields"],
            child["field_map"],
            frappe.get_meta(child_df.options),
            "plan.map.child_table",
        )

    return plan


# ---------------------------------------------------------------------------
# Stages 3 & 4 — EXTRACT (LLM) and UPSERT (pure code, permission-fenced)
# ---------------------------------------------------------------------------


def _propose(doc, mapping: dict, entries: list[tuple[str, dict, float | None]]) -> str:
    """Park one `Crema Proposal` row per entry instead of writing it — reached from
    `_execute` for a plan row and from `_run_inside`'s File Query branch for a file
    record. Returns a `_summary`-shaped string for `last_result`.

    `entries` is (source_id, payload, confidence). `source_id` plus the payload's own
    JSON is what makes `fingerprint` stable across a repeated run — see the module
    docstring's Propose Only bullet: a plan row's source_id is the extraction call's own
    identity (shared by every row from one run, so the payload tells them apart); a file
    record's is the File document's `name` (_read_files already has it). The payload
    never keeps `confidence`/`_source_file` — those are propose-only bookkeeping, not
    part of what the writer will see once approved.

    A duplicate fingerprint — this task, this source, this exact payload, seen before,
    approved or not — inserts nothing and is not an error: that is the crash-recovery
    case PLAN.md item 1 asks for. Any other insert failure IS an error: unlike a skipped
    `_upsert` row, a lost proposal is lost work, so it propagates into
    `_plan_and_execute`/`_run_inside` and the run records Failed, same as an `_upsert`
    failure today.
    """
    counts = {"proposed": 0, "already proposed": 0}
    for source_id, payload, confidence in entries:
        payload = {k: v for k, v in payload.items() if k not in ("confidence", "_source_file")}
        fingerprint = hashlib.sha256(
            "|".join([doc.name, source_id, frappe.as_json(payload)]).encode()
        ).hexdigest()
        proposal = frappe.get_doc(
            {
                "doctype": "Crema Proposal",
                "task": doc.name,
                "target_doctype": mapping["doctype"],
                "confidence": confidence,
                "fingerprint": fingerprint,
                "payload_json": frappe.as_json(payload),
                "mapping_json": frappe.as_json(mapping),
            }
        )
        try:
            proposal.insert(ignore_permissions=True)
            counts["proposed"] += 1
        except frappe.UniqueValidationError:
            counts["already proposed"] += 1
    return _summary(counts)


def _lock_proposal(name: str) -> Any:
    """Read one `Crema Proposal`'s mutable fields under a row lock, so two concurrent
    approvals (or undos) of the same row can't both pass the status guard, both write,
    and have the second `db_set` overwrite the first one's `created_json`.

    Same idiom, and the same reasoning, as CremaLog._tail_chain_state: the lock is held
    to the end of the calling request's transaction (frappe commits once, at the end),
    so a second caller blocks here until the first one finishes and then reads the
    status the first one stamped, instead of the Pending it saw a moment earlier.

    `status` and `created_json` come off this locked row, never off the `frappe.get_doc`
    after it: a plain read can serve an older snapshot of the same row, which is exactly
    the stale value the lock exists to keep out. Every other field of a proposal is set
    at insert and never updated, so reading those from the document is safe.

    Returns an empty row for a name that does not exist; the caller's `frappe.get_doc`
    raises `DoesNotExistError` for that case, as it did before the lock.
    """
    row = frappe.db.sql(
        "select status, created_json from `tabCrema Proposal` where name = %s for update",
        name,
        as_dict=True,
    )
    return row[0] if row else frappe._dict()


def apply_proposal(name: str) -> str:
    """Approve one `Crema Proposal`: replay it through the writer a `Create or Update
    Records` run would have used, then stamp the outcome. Reached from
    `api.approve_proposals`, which is the only caller and already holds the System
    Manager fence.

    Runs inside the task's own sandbox, as its own isolation user — an approval is a
    write this task's account is allowed to make, same as an unattended run's. The
    reentrancy guard is set for the same reason `run_task` sets it (see `on_doc_event`):
    approving is the run's write, made later and by a human's click instead of the
    scheduler, and must not re-trigger a Document Event task watching this doctype.

    The status guard reads the row under a lock (`_lock_proposal`), so two overlapping
    approvals of one proposal write once, not twice.
    """
    locked = _lock_proposal(name)
    proposal = frappe.get_doc("Crema Proposal", name)
    if locked.status != "Pending":
        raise AutomationError(f"'{name}' is already {locked.status}.")

    doc = frappe.get_doc("Crema Automation Task", proposal.task)
    cfg = client._resolve(doc.interface)
    cfg = {**cfg, "isolation_user": doc.isolation_user(cfg)}
    mapping = frappe.parse_json(proposal.mapping_json)
    payload = frappe.parse_json(proposal.payload_json)
    written = _Written(doc.name)

    frappe.local.crema_in_automation = True
    try:
        with sandbox.isolation(cfg["isolation_user"]):
            if "field_map" in mapping:
                outcome = _summary(_upsert(mapping, [payload], written=written))
            else:
                outcome = _summary(
                    _upsert_files(mapping["doctype"], [payload], mapping["match_fields"], written=written)
                )
    finally:
        frappe.local.crema_in_automation = False

    proposal.db_set(
        {"status": "Approved", "outcome": outcome, "created_json": frappe.as_json(written.created)}
    )
    return outcome


def discard_proposal(name: str) -> None:
    """Discard one `Crema Proposal` — no writer involved. Reached from
    `api.discard_proposals`.

    Locked the same way as `apply_proposal`/`undo_proposal`: an unlocked read here could
    race an overlapping `apply_proposal` on the same row — discard reads Pending, approve
    commits a real write, discard's unconditional `db_set` then stamps Discarded over a
    row that was just Approved, orphaning the created record with no Undo path (Undo only
    ever shows for Approved).
    """
    locked = _lock_proposal(name)
    proposal = frappe.get_doc("Crema Proposal", name)
    if locked.status != "Pending":
        raise AutomationError(f"'{name}' is already {locked.status}.")
    proposal.db_set("status", "Discarded")


def _undo_created(created: list[list[str]]) -> tuple[list[list[str]], list[str]]:
    """Delete every [doctype, name] pair. `frappe.delete_doc` keeps its own fences on —
    the isolation user's permissions and the link-exists check — so a record another
    document now links to is refused, not force-deleted. A pair that fails to delete
    stays in the returned remaining list, so the person can clear the reason and retry
    undo later."""
    remaining, failed = [], []
    for doctype, name in created:
        try:
            frappe.delete_doc(doctype, name, force=0, ignore_permissions=False, ignore_missing=True)
        except Exception as exc:
            remaining.append([doctype, name])
            failed.append(f"{doctype} {name}: {_describe(exc)}")
    return remaining, failed


def undo_last_run(task: str) -> dict[str, Any]:
    """Delete every record the task's last run created; leave every record it updated
    alone, for a person to review by hand — see PLAN.md's "Undo a run". Reached from
    `api.undo_last_run`, which already holds the System Manager fence.

    Runs inside the task's own sandbox, as its own isolation user, the same as
    `apply_proposal`: undoing is the reverse of a write that account was allowed to make,
    and no more.
    """
    doc = frappe.get_doc("Crema Automation Task", task)
    written = frappe.parse_json(doc.last_written_json or "{}")
    created = written.get("created") or []
    if not created:
        raise AutomationError(f"'{task}' has no created records to undo.")

    cfg = client._resolve(doc.interface)
    cfg = {**cfg, "isolation_user": doc.isolation_user(cfg)}

    frappe.local.crema_in_automation = True
    try:
        with sandbox.isolation(cfg["isolation_user"]):
            remaining, failed = _undo_created(created)
    finally:
        frappe.local.crema_in_automation = False

    doc.db_set(
        "last_written_json",
        frappe.as_json(
            {
                "created": remaining,
                "updated": written.get("updated") or [],
                "truncated": written.get("truncated", False),
                "undone_at": now_datetime().isoformat(),
            }
        ),
    )
    return {"deleted": len(created) - len(remaining), "failed": failed}


def undo_proposal(name: str) -> dict[str, Any]:
    """Reverse one Approved `Crema Proposal`: delete what approving it created, then
    return the row to Pending so it can be approved again or discarded. Reached from
    `api.undo_proposals`. `fingerprint` is untouched and stays unique, so a repeated
    source read still finds this row instead of parking a duplicate.

    The status guard reads the row under a lock (`_lock_proposal`), so two overlapping
    undos of one proposal delete once, not twice."""
    locked = _lock_proposal(name)
    proposal = frappe.get_doc("Crema Proposal", name)
    if locked.status != "Approved":
        raise AutomationError(f"'{name}' is not Approved.")

    doc = frappe.get_doc("Crema Automation Task", proposal.task)
    cfg = client._resolve(doc.interface)
    cfg = {**cfg, "isolation_user": doc.isolation_user(cfg)}
    created = frappe.parse_json(locked.created_json or "[]")

    frappe.local.crema_in_automation = True
    try:
        with sandbox.isolation(cfg["isolation_user"]):
            remaining, failed = _undo_created(created)
    finally:
        frappe.local.crema_in_automation = False

    if remaining:
        proposal.db_set("created_json", frappe.as_json(remaining))
    else:
        proposal.db_set({"status": "Pending", "outcome": None, "created_json": None})
    return {"deleted": len(created) - len(remaining), "failed": failed}


def _execute(
    doc, plan: dict, content: str, allowed_names: set[str] | None, written: _Written | None = None
) -> str:
    """Run stage 4 and return a human-readable count for `last_result`."""
    mapping = plan["map"]
    if doc.action == "Propose Only":
        source_id = hashlib.sha256(f"{plan['extract']['prompt']}\n\n{content}".encode()).hexdigest()
        rows = _extract(doc, plan, content)
        return _propose(doc, mapping, [(source_id, row, None) for row in rows])
    if doc.action != "Update the Records It Read":
        return _summary(_upsert(mapping, _extract(doc, plan, content), written=written))

    # Checked before the extraction call, so a plan of the wrong shape costs nothing.
    # Raised, not thrown: this lands in _plan_and_execute's replan, so the failure text
    # goes back to the planner and it corrects itself on the same run.
    if list(mapping["match_fields"]) != ["name"]:
        raise AutomationError('an update-source plan must match on ["name"]')

    # A document query reads parent fields only, so the model has never seen the existing
    # child rows — a child_table here would append against nothing it can dedup on.
    mapping = {key: value for key, value in mapping.items() if key != "child_table"}
    rows = _extract(doc, plan, content)
    return _summary(_upsert(mapping, rows, create=False, allowed_names=allowed_names, written=written))


def _summary(counts: dict[str, int]) -> str:
    return ", ".join(f"{n} {label}" for label, n in counts.items() if n) or "nothing to do"


def _extract(doc, plan: dict, content: str) -> list[dict]:
    extract = plan["extract"]
    kwargs = {}
    if schema := extract.get("response_schema"):
        kwargs["response_format"] = schema

    # _PLAN_CONTENT_CHARS caps the *planning* prompt only; without this a 2 MB page or 50
    # whole documents went to the extractor in full.
    content = content[:_EXTRACT_CONTENT_CHARS]
    result = api.ask_json(doc.interface, f"{extract['prompt']}\n\n{content}", **kwargs)
    rows = result.get("rows") if isinstance(result, dict) else None
    rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    if not rows:
        raise AutomationError("extraction returned no usable rows")
    return rows


def _row_values(field_map: dict[str, str], row: dict) -> dict[str, Any]:
    return {fieldname: row[key] for key, fieldname in field_map.items() if key in row}


def _match_existing(
    doctype: str,
    values: dict[str, Any],
    match_fields: list[str],
    *,
    allowed_names: set[str] | None = None,
    create: bool = True,
) -> str | bool | None:
    """The identify-or-skip fence shared by _upsert (an LLM-authored field_map row) and
    _upsert_files (an extract() record, already fieldname-keyed by _filter_diff). Returns
    the matched record's name, None if none exists (create one), or False if this row
    must be skipped instead.

    `create=False` and `allowed_names` are the `Update the Records It Read` fences: never
    create, and never touch a record the source query did not return — checked after the
    lookup, so the fence holds whatever match fields were actually used.
    """
    filters = {fieldname: values.get(fieldname) for fieldname in match_fields}
    if any(value is None or isinstance(value, dict | list) for value in filters.values()):
        # None: the row can't be identified — skip rather than create junk. dict/list:
        # frappe reads a list filter value as ["operator", ...], so a row could smuggle
        # ["like", "%"] into the match and hit an arbitrary record instead of one equal
        # to the extracted value.
        return False

    # get_list, not get_all: these filters are built from LLM-extracted values taken from
    # fetched (attacker-influenceable) content, and get_all ignores permissions — it would
    # happily locate and load a record the isolation user cannot see.
    existing = frappe.get_list(doctype, filters=filters, limit=1, pluck="name")

    if allowed_names is not None and (not existing or existing[0] not in allowed_names):
        return False
    if not existing and not create:
        return False
    return existing[0] if existing else None


def _upsert(
    mapping: dict,
    rows: list[dict],
    *,
    create: bool = True,
    allowed_names: set[str] | None = None,
    written: _Written | None = None,
) -> dict[str, int]:
    """Find-or-create one record per row, then (optionally) upsert one child row from the
    same row. Plain `doc.save()` — permissions are the isolation user's. Returns counts.
    `written`, if given, is appended with each saved record's (doctype, name) — see
    `_Written`."""
    doctype = mapping["doctype"]
    child = mapping.get("child_table")
    counts = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0}

    for row in rows:
        values = _row_values(mapping["field_map"], row)
        match = _match_existing(
            doctype, values, mapping["match_fields"], allowed_names=allowed_names, create=create
        )
        if match is False:
            counts["skipped"] += 1
            continue

        # `name` identifies a record; it is never written. doc.update({"name": ...})
        # mutates self.name, and BaseDocument.db_update then writes THIS document's values
        # onto whatever row the model named — with no permission check on that row.
        values.pop("name", None)

        doc = frappe.get_doc(doctype, match) if match else frappe.new_doc(doctype)
        if match and not child and _unchanged(doc, values):
            # Skipping the save is what stops an incremental Update Source task from
            # bumping `modified` on every run and re-reading its own output forever. It
            # also makes the replan-after-partial-failure path idempotent.
            counts["unchanged"] += 1
            continue

        doc.update(values)
        if child:
            _upsert_child(doc, child, row)
        _stamp(doc, written)
        doc.save()
        counts["updated" if match else "created"] += 1
        if written is not None:
            written.record(doctype, doc.name, created=not match)

    return counts


def _upsert_files(
    doctype: str, records: list[dict], match_fields: list[str], *, written: _Written | None = None
) -> dict[str, int]:
    """The File Query writer. `records` are already {"set", "child_set"} dicts straight
    from api.extract() — _filter_diff has already sanitised every key against the target
    (and child) doctype's own meta, so there is no field_map to apply, unlike _upsert.
    Shares _match_existing's identify-or-skip fence: a File Query always creates (never
    `Update the Records It Read` — see CremaAutomationTask._validate_action), so
    `allowed_names` is never passed here."""
    counts = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0}

    for record in records:
        values = {k: v for k, v in (record.get("set") or {}).items() if k != "name"}
        match = _match_existing(doctype, values, match_fields)
        if match is False:
            counts["skipped"] += 1
            continue

        child_set = record.get("child_set") or {}
        doc = frappe.get_doc(doctype, match) if match else frappe.new_doc(doctype)
        if match and not child_set and _unchanged(doc, values):
            counts["unchanged"] += 1
            continue

        doc.update(values)
        _replace_children(doc, child_set)
        _stamp(doc, written)
        doc.save()
        counts["updated" if match else "created"] += 1
        if written is not None:
            written.record(doctype, doc.name, created=not match)

    return counts


def _replace_children(doc, child_set: dict[str, list[dict]]) -> None:
    """The file is the whole truth for its own line items — replace, don't merge."""
    for fieldname, child_rows in child_set.items():
        doc.set(fieldname, [])
        for child_row in child_rows:
            doc.append(fieldname, child_row)


def _unchanged(doc, values: dict[str, Any]) -> bool:
    return all(str(doc.get(fieldname) or "") == str(value or "") for fieldname, value in values.items())


def dry_run(task: str) -> dict[str, Any]:
    """Everything run_task does up to and including EXTRACT, and nothing after it — no
    record is created or updated. Reached from the form's Dry Run button.

    The plan it settles on IS stored: reviewing plan A in a dialog and then letting the
    3am run write plan B would defeat the point of previewing at all.
    """
    doc = frappe.get_doc("Crema Automation Task", task)
    cfg = client._resolve(doc.interface)
    cfg = {**cfg, "isolation_user": doc.isolation_user(cfg)}

    with sandbox.isolation(cfg["isolation_user"]):
        content, allowed_names, note, _, records = _read_source(
            doc, cfg, now_datetime(), None, None, preview=True
        )
        if not content.strip() and not records:
            return {"action": doc.action, "row_count": 0, "note": note or "no records matched"}

        if doc.file_sources():
            # No plan for a File Query — each file already reads as a full {"set",
            # "child_set"} record via api.extract(), so (unlike the plan-based rows
            # below) the preview can show child rows too. crema_show_dry_run treats a
            # missing used_stored_plan key as "there is no plan to describe" rather than
            # "a new one was written".
            return {
                "action": doc.action,
                "rows": records[:20],
                "row_count": len(records),
                "note": note,
            }

        if doc.action == "No Changes":
            return {"action": doc.action, "report": api.ask(doc.interface, doc.instruction, context=content)}

        stored = _stored_plan(doc)
        plan = stored if stored is not None else _make_plan(doc, content)
        rows = _extract(doc, plan, content)

    if stored is None:
        doc.db_set("plan_json", frappe.as_json(plan))

    return {
        "action": doc.action,
        "used_stored_plan": stored is not None,
        "plan": plan,
        "rows": rows[:20],
        "row_count": len(rows),
        "allowed_names": len(allowed_names) if allowed_names is not None else None,
        "note": note,
    }


def _upsert_child(doc, child: dict, row: dict) -> None:
    values = _row_values(child["field_map"], row)
    if not values:
        return

    def matches(child_row) -> bool:
        return all(str(child_row.get(f) or "") == str(values.get(f) or "") for f in child["match_fields"])

    existing = next((r for r in doc.get(child["fieldname"]) or [] if matches(r)), None)
    if existing:
        existing.update(values)
    else:
        doc.append(child["fieldname"], values)
