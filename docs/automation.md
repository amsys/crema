# Automate with Crema

A **Crema Automation Task** fetches a URL, reads it with an LLM, and writes the
result into a doctype, on a schedule. Each task runs four steps in order: FETCH,
PLAN, EXTRACT, UPSERT.

## Crema Automation Task fields

| Field | Type | Notes |
|---|---|---|
| `task_name` | Data | Required. Unique. |
| `enabled` | Check | Off by default. |
| `source_url` | Data | Required. The only URL this task reads. Only a System Manager can set it. |
| `schedule` | Data | Required. A cron expression. See "Schedule limits" below. |
| `interface` | Autocomplete | Required. One of the predefined interface names (see [configure.md](configure.md)). |
| `target_doctype` | Link (DocType) | Required. The only doctype this task can write to. |
| `instruction` | Text | Required. What to read from the page and where to put it. |
| `plan_json` | Code (read-only) | Written by the system on the first run. Clear it to force a new plan. |
| `last_run` | Datetime (read-only) | |
| `last_status` | Select (read-only) | `Success`, `Replanned`, or `Failed`. |
| `consecutive_failures` | Int (read-only) | The task turns itself off at 5. |
| `last_error` | Small Text (read-only) | |

## Create a task

1. Open the **Crema Automation Task** list and create a new one.
2. Fill in `source_url`, `schedule`, `interface`, `target_doctype`, and
   `instruction`.
3. Save the document.
4. Click **Run Now** to run the task once, right away.

## The plan

The first run has no stored plan. The system writes one: an extraction prompt, and a
mapping from the page content to `target_doctype`'s fields. The system checks every
doctype name and field name in this plan against the site's own metadata before it
saves the plan. A plan that names an unknown field or doctype is rejected.

Every run after the first reuses the stored plan. The system does not plan again,
unless a run fails. After a failure, the system plans once more, with the failure
message added, then gives up if that also fails.

## Schedule limits

- The scheduler ticks every 15 minutes. A `schedule` set finer than 15 minutes still
  runs once per tick, not more often.
- The task turns itself off after 5 runs in a row fail.
- `target_doctype` is the only doctype a task can write to. The plan cannot name a
  different one.
- `source_url` is the only URL a task reads. Only a System Manager can set it; the
  LLM never supplies a URL.

## Worked example

Fetch a government public-holidays page, and keep a Holiday List doctype up to date.

1. Create a Crema Automation Task named `Public Holidays 2026`.
2. Set `source_url` to the holidays page.
3. Set `instruction` to `Parse public holidays, update Holiday List 2026`.
4. Set `target_doctype` to `Holiday List`.
5. Pick an interface, for example `extraction`.
6. Save the document, then click **Run Now**.

The first run writes a plan and applies it. Every later scheduled run reuses that
plan.

## Log retention

A daily job deletes Crema Log rows older than `Crema Settings.log_retention_days` (30
days by default — change it on the Crema Settings page).
