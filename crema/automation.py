"""Scheduler automation: FETCH -> (self-)PLAN -> EXTRACT -> UPSERT, with one replan.

Security posture — the reason this module is longer than "just call the LLM":

* The LLM output NEVER becomes code. A plan is *parameters only* (an extract prompt,
  a response schema, and fieldname->fieldname maps) consumed by the fixed three-stage
  pipeline below. `_validate_plan` rejects any key it does not know about, so a plan
  containing e.g. "code" or "script" is refused before it is ever stored.
* Every doctype/fieldname string that came from the LLM is checked against
  `frappe.get_meta` before it reaches the database, both for the parent and for the
  child table.
* The only URL ever fetched is `task.source_url`, entered by a System Manager. The LLM
  cannot supply a URL.
* Everything after the fetch runs inside `sandbox.isolation(<interface isolation
  user>)` and writes with a plain `doc.save()` — no `ignore_permissions` anywhere, so
  the isolation user's roles and User Permissions are the hard fence.

The fetch itself is deliberately *outside* the sandbox: it is pure code with no LLM
input, and outbound HTTP is a system-level capability, not a document permission.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import requests
from croniter import croniter

import frappe
from crema import _ocr, api, client, log, sandbox
from frappe.utils import add_days, now_datetime, strip_html

_PLAN_CONTENT_CHARS = 8000
_MAX_FAILURES = 5
_LOG_RETENTION_DAYS = 30  # fallback if Crema Settings.log_retention_days is unset
_ERROR_MAX_CHARS = 2000
_FETCH_TIMEOUT = 60

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

_PLAN_RULES = """You are configuring a FIXED three-stage pipeline: FETCH (already done) -> EXTRACT -> UPSERT.
You do not write code, you do not invent stages, you only fill in the parameters below.
Your EXTRACT prompt must make the model return {"rows": [ ... ]} — a flat list of row objects.
Each row is upserted into one record of the target doctype; when a child table is configured,
the same row also contributes one child row to that record.
Output ONLY JSON with exactly this shape (omit "child_table" if the target has no child table):"""


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
        filters={"enabled": 1},
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


def enqueue_task(task: str) -> str:
    """Queue one run of `task`. Deduplicated — a task already queued or running is not
    queued twice. Returns the job id."""
    job_id = f"crema-task-{task}"
    job = frappe.enqueue(
        "crema.automation.run_task",
        task=task,
        queue="long",
        timeout=1800,
        job_id=job_id,
        deduplicate=True,
    )
    return getattr(job, "id", None) or job_id


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def run_task(task: str) -> str:
    """Run one automation task end to end. Returns the recorded status."""
    doc = frappe.get_doc("Crema Automation Task", task)

    # Stamp last_run first and commit: a task that explodes must not hot-loop on tick().
    doc.db_set("last_run", now_datetime(), update_modified=False)
    frappe.db.commit()

    try:
        content = _fetch(doc.source_url)
    except Exception as exc:
        return _record(doc, "Failed", f"fetch failed — {_describe(exc)}")

    try:
        isolation_user = client._resolve(doc.interface)["isolation_user"]
    except Exception as exc:
        # e.g. the interface's provider was disabled. Left uncaught this skipped
        # _record() entirely, so last_status stayed stale and the 5-failure
        # auto-disable never fired while tick() re-enqueued the task every 15 minutes.
        return _record(doc, "Failed", f"interface unresolvable — {_describe(exc)}")

    with sandbox.isolation(isolation_user):
        status, error, plan = _plan_and_execute(doc, content)

    if plan is not None:
        doc.db_set("plan_json", frappe.as_json(plan))
    return _record(doc, status, error)


def _plan_and_execute(doc, content: str) -> tuple[str, str, dict | None]:
    """Execute the stored plan (planning first if there is none); on any failure,
    replan exactly once with the failure text appended and try again.

    Returns (status, error, plan_to_store). `plan_to_store` is None when the stored
    plan is still the right one — a plan is only persisted once it has actually run.
    """
    stored = _stored_plan(doc)
    try:
        plan = stored if stored is not None else _make_plan(doc, content)
        _execute(doc, plan, content)
        return "Success", "", None if stored is not None else plan
    except Exception as exc:
        first_error = _describe(exc)

    try:
        plan = _make_plan(doc, content, failure=first_error)
        _execute(doc, plan, content)
        return "Replanned", "", plan
    except Exception as exc:
        return "Failed", f"{first_error} || replan: {_describe(exc)}", None


def _record(doc, status: str, error: str) -> str:
    """Persist the outcome. Five consecutive failures disable the task."""
    if status == "Failed":
        failures = (doc.consecutive_failures or 0) + 1
        doc.db_set(
            {
                "last_status": "Failed",
                "last_error": error[:_ERROR_MAX_CHARS],
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
        doc.db_set({"last_status": status, "last_error": None, "consecutive_failures": 0})
    return status


def _describe(exc: Exception) -> str:
    return log.redact(f"{type(exc).__name__}: {exc}")[:_ERROR_MAX_CHARS]


# ---------------------------------------------------------------------------
# Stage 1 — FETCH (pure code, no LLM involved)
# ---------------------------------------------------------------------------


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


def _planning_prompt(doc, content: str, failure: str | None) -> str:
    parts = [_PLAN_RULES, _PLAN_SHAPE, f"Task instruction:\n{doc.instruction}"]
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

    known = {df.fieldname for df in meta.fields}
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
    if target_doctype and doctype != target_doctype:
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


def _execute(doc, plan: dict, content: str) -> None:
    _upsert(plan["map"], _extract(doc, plan, content))


def _extract(doc, plan: dict, content: str) -> list[dict]:
    extract = plan["extract"]
    kwargs = {}
    if schema := extract.get("response_schema"):
        kwargs["response_format"] = schema

    result = api.ask_json(doc.interface, f"{extract['prompt']}\n\n{content}", **kwargs)
    rows = result.get("rows") if isinstance(result, dict) else None
    rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    if not rows:
        raise AutomationError("extraction returned no usable rows")
    return rows


def _row_values(field_map: dict[str, str], row: dict) -> dict[str, Any]:
    return {fieldname: row[key] for key, fieldname in field_map.items() if key in row}


def _upsert(mapping: dict, rows: list[dict]) -> None:
    """Find-or-create one record per row, then (optionally) upsert one child row from the
    same row. Plain `doc.save()` — permissions are the isolation user's."""
    doctype = mapping["doctype"]
    child = mapping.get("child_table")

    for row in rows:
        values = _row_values(mapping["field_map"], row)
        filters = {fieldname: values.get(fieldname) for fieldname in mapping["match_fields"]}
        if any(value is None for value in filters.values()):
            continue  # row can't be identified — skip rather than create a junk record

        # get_list, not get_all: these filters are built from LLM-extracted values taken
        # from fetched (attacker-influenceable) content, and get_all ignores permissions
        # — it would happily locate and load a record the isolation user cannot see.
        existing = frappe.get_list(doctype, filters=filters, limit=1, pluck="name")
        doc = frappe.get_doc(doctype, existing[0]) if existing else frappe.new_doc(doctype)
        doc.update(values)

        if child:
            _upsert_child(doc, child, row)

        doc.save()


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
