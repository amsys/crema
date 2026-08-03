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

The three actions differ only in what stage 4 does with the extracted rows:

* `Create or Update Records` — find-or-create in `target_doctype`.
* `Update the Records It Read` — update, never create, and only records whose `name` the
  source query actually returned (`allowed_names`). `name` is usable as a *match* field
  but is never written: `doc.update({"name": ...})` mutates `self.name`, and
  `BaseDocument.db_update` would then write this document's values onto whatever row
  the model named, with no permission check on that row.
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

import mimetypes
import re
from datetime import datetime
from typing import Any

import requests
from croniter import croniter

import frappe
from crema import _ocr, api, client, log, sandbox, security
from frappe.model import data_fieldtypes
from frappe.utils import add_days, cint, now_datetime, strip_html

_PLAN_CONTENT_CHARS = 8000
_EXTRACT_CONTENT_CHARS = 60_000
_MAX_FAILURES = 5
_LOG_RETENTION_DAYS = 30  # fallback if Crema Settings.log_retention_days is unset
_ERROR_MAX_CHARS = 2000
_RESULT_MAX_CHARS = 20_000
_FETCH_TIMEOUT = 60
_SOURCE_LIMIT_DEFAULT = 50
# One OCR call per attachment, billed and logged like any other. A record with a dozen
# scans attached would otherwise quietly multiply the cost of one run by twelve.
_ATTACHMENT_MAX_PER_RECORD = 5
_WEBHOOK_PAYLOAD_CHARS = 20_000

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
    """Enqueue every enabled task whose cron schedule is due. Runs every 15 minutes —
    that tick interval is the effective granularity floor for `schedule`."""
    now = now_datetime()
    for task in frappe.get_all(
        "Crema Automation Task",
        filters={"enabled": 1, "trigger": "Schedule"},
        fields=["name", "schedule", "last_run", "creation"],
    ):
        try:
            due_at = croniter(task.schedule, task.last_run or task.creation).get_next(datetime)
        except Exception:
            continue  # invalid cron on an existing row — validate() blocks new ones
        if due_at <= now:
            enqueue_task(task.name)


def cleanup_logs() -> None:
    """Daily: drop Crema Log rows older than Crema Settings.log_retention_days."""
    retention_days = frappe.db.get_single_value("Crema Settings", "log_retention_days") or _LOG_RETENTION_DAYS
    frappe.db.delete("Crema Log", {"creation": ("<", add_days(now_datetime(), -retention_days))})


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
    for task in _event_tasks().get((doc.doctype, method), ()):
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
    task — a task watching two doctypes is listed under both."""
    events = {
        task["name"]: task["event"]
        for task in frappe.get_all(
            "Crema Automation Task",
            filters={"enabled": 1, "trigger": "Document Event"},
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
            "source_type": "Document Query",
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
    doc = frappe.get_doc("Crema Automation Task", task)

    # Captured before the read, and used as the new watermark for any source that did not
    # fill its batch: a record changed *during* a long run must be read by the next one.
    started = now_datetime()

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
        return _record(doc, "Failed", f"interface unresolvable — {_describe(exc)}")

    # A copy, not a mutation: _resolve's dict may be shared, and everything downstream —
    # the sandbox, _read_documents' "skip my own writes" filter, _ocr's file loading —
    # reads the isolation user from cfg, so the task's own Runs As has to land here.
    cfg = {**cfg, "isolation_user": doc.isolation_user(cfg)}

    # The guard must cover every write this run makes, including _record's own db_set.
    frappe.local.crema_in_automation = True
    try:
        with sandbox.isolation(cfg["isolation_user"]):
            status, error, plan, result, watermarks = _run_inside(
                doc, cfg, started, doc_doctype, doc_name, payload
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
        outcome = _record(doc, status, error, result)
    finally:
        frappe.local.crema_in_automation = False

    _notify(doc, status, result)
    return outcome


def _run_inside(
    doc, cfg: dict, started, doc_doctype: str | None, doc_name: str | None, payload: str | None = None
) -> tuple[str, str, dict | None, str, dict[str, Any]]:
    """Everything that runs as the isolation user. Returns
    (status, error, plan_to_store, result, watermarks)."""
    try:
        content, allowed_names, note, watermarks = _read_source(
            doc, cfg, started, doc_doctype, doc_name, payload=payload
        )
    except Exception as exc:
        return "Failed", f"source read failed — {_describe(exc)}", None, "", {}

    if not content.strip():
        # The normal state of a quiet incremental task. Left to fall through it would
        # cost two LLM calls, record Failed, and auto-disable the task after five quiet
        # nights. The watermarks still propagate: a batch whose records were all dropped
        # by the scan must still advance, or the backlog behind it is never read.
        return "Success", "", None, note or "no new records", watermarks

    if doc.action == "No Changes":
        try:
            report = api.ask(doc.interface, doc.instruction, context=content)
        except Exception as exc:
            return "Failed", _describe(exc), None, "", {}
        return "Success", "", None, _join_note(report[:_RESULT_MAX_CHARS], note), watermarks

    status, error, plan, result = _plan_and_execute(doc, content, allowed_names)
    return status, error, plan, _join_note(result, note), watermarks


def _join_note(result: str, note: str) -> str:
    return f"{result} ({note})" if result and note else result or note


def _plan_and_execute(doc, content: str, allowed_names: set[str] | None) -> tuple[str, str, dict | None, str]:
    """Execute the stored plan (planning first if there is none); on any failure,
    replan exactly once with the failure text appended and try again.

    Returns (status, error, plan_to_store, result). `plan_to_store` is None when the
    stored plan is still the right one — a plan is only persisted once it has actually run.
    """
    stored = _stored_plan(doc)
    try:
        plan = stored if stored is not None else _make_plan(doc, content)
        result = _execute(doc, plan, content, allowed_names)
        return "Success", "", None if stored is not None else plan, result
    except Exception as exc:
        first_error = _describe(exc)

    try:
        plan = _make_plan(doc, content, failure=first_error)
        result = _execute(doc, plan, content, allowed_names)
        return "Replanned", "", plan, result
    except Exception as exc:
        return "Failed", f"{first_error} || replan: {_describe(exc)}", None, ""


def _record(doc, status: str, error: str, result: str = "") -> str:
    """Persist the outcome. Five consecutive failures disable the task."""
    if status == "Failed":
        failures = (doc.consecutive_failures or 0) + 1
        doc.db_set(
            {
                "last_status": "Failed",
                "last_error": error[:_ERROR_MAX_CHARS],
                "last_result": result[:_RESULT_MAX_CHARS] or None,
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


def _describe(exc: Exception) -> str:
    return log.redact(f"{type(exc).__name__}: {exc}")[:_ERROR_MAX_CHARS]


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
) -> tuple[str, set[str] | None, str, dict[str, Any]]:
    """Read every source row of this task, as one labelled text. Returns
    (content, allowed_names, note, watermarks).

    `allowed_names` is None when no document query contributed, and otherwise the exact
    set of record names the queries returned — it is what fences `Update the Records It Read`
    (which validate() holds to a single query source, so the set is one query's).

    `watermarks` is {source row name: read up to}, applied by run_task only if the run as
    a whole succeeded. A source that raised is absent from it, so it is re-read next time.

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
    blocks, allowed_names, notes, watermarks, failures = [], None, [], {}, []

    if payload:
        blocks.append(f"=== Source: webhook payload ===\n{payload[:budget]}")

    for source in doc.sources:
        try:
            if source.source_type != "Document Query":
                text, names, note, read_up_to = _fetch(source.source_url)[:budget], None, "", None
            else:
                # An event run reads the one record that changed — but only from the
                # source that watches its doctype; a second source is reference material
                # and must still be read in full.
                narrow = doc_name if source.source_doctype == doc_doctype else None
                text, names, note, read_up_to = _read_documents(source, cfg, started, narrow, budget, preview)
        except Exception as exc:
            failures.append(f"{source.source_label or source.source_type} failed — {_describe(exc)}")
            if doc.on_source_error == "Fail the Run":
                raise
            continue

        if text:
            blocks.append(f"=== Source: {source.source_label} ===\n{text}")
        if names is not None:
            allowed_names = names if allowed_names is None else allowed_names | names
        if note:
            notes.append(f"{source.source_label}: {note}")
        if read_up_to:
            watermarks[source.name] = read_up_to

    if failures and len(failures) == len(doc.sources) and not payload:
        raise AutomationError("; ".join(failures))

    return "\n\n".join(blocks), allowed_names, "; ".join(notes + failures), watermarks


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


def _normalized_filters(raw: Any) -> list[list]:
    """One filter shape downstream. frappe.ui.FilterGroup writes a list of
    [fieldname, operator, value] (or 4-element, doctype-prefixed) rows; a hand-written
    dict is also accepted. Normalizing to the list form means an added filter ANDs with a
    user-supplied one on the same field instead of clobbering it."""
    if isinstance(raw, dict):
        return [[fieldname, "=", value] for fieldname, value in raw.items()]
    return [list(row) for row in raw or [] if isinstance(row, list | tuple)]


def _read_documents(
    source, cfg: dict, started, doc_name: str | None, budget: int, preview: bool = False
) -> tuple[str, set[str], str, Any]:
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
    rows = frappe.get_list(
        source.source_doctype,
        filters=filters,
        fields=_source_fields(source.source_doctype),
        limit=limit,
        order_by="modified asc",
    )

    # A capped batch read the head of the backlog only, so the next run resumes at the
    # last row it actually handled; an uncapped one read everything up to the moment this
    # run started. `started`, not `now`: a record changed during a long run is not lost.
    read_up_to = (rows[-1].get("modified") if len(rows) == limit else started) if incremental else None
    if not rows:
        return "", set(), "", read_up_to

    kept, dropped = rows, 0
    if cfg["enable_prompt_scan"]:
        # Triage, not the fence — api.ask_json still scans the assembled content. Without
        # it one soft hyphen in one record blocks all 50, and five such runs disable the
        # task. Only meaningful when the interface actually has layer 1 on.
        kept = [row for row in rows if not security.scan(frappe.as_json(row))]
        dropped = len(rows) - len(kept)

    notes = [f"{dropped} skipped by the security scan"] if dropped else []
    if not kept:
        return "", set(), "; ".join(notes), read_up_to

    text = frappe.as_json(kept)
    if source.read_attachments:
        attachments, attachment_note = _read_attachments(source.source_doctype, kept)
        text = f"{text}\n{attachments}" if attachments else text
        if attachment_note:
            notes.append(attachment_note)

    return text[:budget], {row["name"] for row in kept}, "; ".join(notes), read_up_to


def _read_attachments(doctype: str, rows: list) -> tuple[str, str]:
    """OCR the files attached to each record and return them as labelled text blocks.

    Attachments are a universal frappe concept — the sidebar, a drag-drop and an inbound
    email all produce the same File rows — so this is not an email feature. It is what
    makes "an invoice arrives by mail, read it into a record" reachable without code: an
    Email Account writes one Communication per message and attaches its files to it.

    frappe.get_list, not frappe.desk.form.load.get_attachments: that helper uses get_all,
    which ignores permissions, and everything inside sandbox.isolation goes through the
    permission engine. Note the separate, already-documented limit that _ocr._load_bytes
    reads a File's bytes off disk without its own permission check — this listing is the
    fence, so it must be the permission-aware one.
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
            mime = mimetypes.guess_type(file.file_name or "")[0] or ""
            # _ocr.prep_parts returns [] for anything that is not a PDF or an image, and
            # ocr() would still bill a provider call for the empty result.
            if mime != "application/pdf" and not mime.startswith("image/"):
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


def _fetch(url: str) -> str:
    """Fetch `url` and reduce it to plain text. PDFs go through the crema._ocr pipeline
    (embedded text when present, vision OCR when scanned), HTML is stripped, anything
    else is used as-is."""
    response = requests.get(url, timeout=_FETCH_TIMEOUT)
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

    meta = frappe.get_meta(doctype)
    _validate_field_maps(mapping["match_fields"], mapping["field_map"], meta, "plan.map")

    child = mapping.get("child_table")
    if child is not None:
        _require_keys(child, _CHILD_KEYS, _CHILD_KEYS, "plan.map.child_table")
        child_df = meta.get_field(child["fieldname"])
        if not child_df or child_df.fieldtype != "Table":
            raise AutomationError(f"plan.map.child_table.fieldname '{child['fieldname']}' is not a table")
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


def _execute(doc, plan: dict, content: str, allowed_names: set[str] | None) -> str:
    """Run stage 4 and return a human-readable count for `last_result`."""
    mapping = plan["map"]
    if doc.action != "Update the Records It Read":
        return _summary(_upsert(mapping, _extract(doc, plan, content)))

    # Checked before the extraction call, so a plan of the wrong shape costs nothing.
    # Raised, not thrown: this lands in _plan_and_execute's replan, so the failure text
    # goes back to the planner and it corrects itself on the same run.
    if list(mapping["match_fields"]) != ["name"]:
        raise AutomationError('an update-source plan must match on ["name"]')

    # A document query reads parent fields only, so the model has never seen the existing
    # child rows — a child_table here would append against nothing it can dedup on.
    mapping = {key: value for key, value in mapping.items() if key != "child_table"}
    rows = _extract(doc, plan, content)
    return _summary(_upsert(mapping, rows, create=False, allowed_names=allowed_names))


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


def _upsert(
    mapping: dict, rows: list[dict], *, create: bool = True, allowed_names: set[str] | None = None
) -> dict[str, int]:
    """Find-or-create one record per row, then (optionally) upsert one child row from the
    same row. Plain `doc.save()` — permissions are the isolation user's. Returns counts.

    `create=False` and `allowed_names` are the `Update the Records It Read` fences: never
    create, and never touch a record the source query did not return.
    """
    doctype = mapping["doctype"]
    child = mapping.get("child_table")
    counts = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0}

    for row in rows:
        values = _row_values(mapping["field_map"], row)
        filters = {fieldname: values.get(fieldname) for fieldname in mapping["match_fields"]}
        if any(value is None or isinstance(value, dict | list) for value in filters.values()):
            # None: the row can't be identified — skip rather than create junk. dict/list:
            # frappe reads a list filter value as ["operator", ...], so a row could
            # smuggle ["like", "%"] into the match and hit an arbitrary record instead of
            # one equal to the extracted value.
            counts["skipped"] += 1
            continue

        # get_list, not get_all: these filters are built from LLM-extracted values taken
        # from fetched (attacker-influenceable) content, and get_all ignores permissions
        # — it would happily locate and load a record the isolation user cannot see.
        existing = frappe.get_list(doctype, filters=filters, limit=1, pluck="name")

        # Checked after the lookup, so the fence holds whatever match_fields the model
        # picked — it never depends on the plan having matched on "name".
        if allowed_names is not None and (not existing or existing[0] not in allowed_names):
            counts["skipped"] += 1
            continue
        if not existing and not create:
            counts["skipped"] += 1
            continue

        # `name` identifies a record; it is never written. doc.update({"name": ...})
        # mutates self.name, and BaseDocument.db_update then writes THIS document's values
        # onto whatever row the model named — with no permission check on that row.
        values.pop("name", None)

        doc = frappe.get_doc(doctype, existing[0]) if existing else frappe.new_doc(doctype)
        if existing and not child and _unchanged(doc, values):
            # Skipping the save is what stops an incremental Update Source task from
            # bumping `modified` on every run and re-reading its own output forever. It
            # also makes the replan-after-partial-failure path idempotent.
            counts["unchanged"] += 1
            continue

        doc.update(values)
        if child:
            _upsert_child(doc, child, row)
        doc.save()
        counts["updated" if existing else "created"] += 1

    return counts


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
        content, allowed_names, note, _ = _read_source(doc, cfg, now_datetime(), None, None, preview=True)
        if not content.strip():
            return {"action": doc.action, "row_count": 0, "note": note or "no records matched"}

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
