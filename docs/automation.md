# Automate with Crema

A **Crema Automation Task** reads a source, sends it to an LLM, and does something
with the result — on a schedule, or when a document changes. Each task runs four
steps in order: SOURCE, PLAN, EXTRACT, WRITE.

Build a task from three choices:

| Choice | Options |
|---|---|
| **Trigger** — when it runs | `Schedule` (a cron expression), or `Document Event` |
| **Source** — what it reads | `URL`, or `Document Query` (records on this site) |
| **Action** — what it does | `Upsert Records`, `Update Source Records`, or `Report Only` |

## Crema Automation Task fields

| Field | Type | Notes |
|---|---|---|
| `task_name` | Data | Required. Unique. |
| `enabled` | Check | Off by default. |
| `trigger` | Select | `Schedule` or `Document Event`. |
| `schedule_preset` | Select | A common schedule. Leave it empty to write the cron expression yourself. |
| `schedule` | Data | The cron expression. The preset fills it in. |
| `event` | Select | For a Document Event trigger: `After Insert`, `On Update`, or `On Submit`. |
| `source_type` | Select | `URL` or `Document Query`. |
| `source_url` | Data | For a URL source. The only URL this task reads. Only a System Manager can set it. |
| `source_doctype` | Link (DocType) | For a Document Query source. Only a System Manager can set it. |
| `source_filters` | Code (JSON) | Which records to read. Click the table to set them. |
| `source_limit` | Int | Most records to read in one run. 50 by default, 200 maximum. |
| `incremental` | Check | Read only records changed since the last run. On by default. |
| `interface` | Autocomplete | Required. One of the interface names (see [configure.md](configure.md)). |
| `instruction` | Text | Required. What to read from the source, and what to do with it. |
| `action` | Select | `Upsert Records`, `Update Source Records`, or `Report Only`. |
| `target_doctype` | Link (DocType) | For `Upsert Records`. The only doctype this task can write to. |
| `notify_to` | Small Text | For `Report Only`. Comma-separated addresses. |
| `plan_json` | Code (read-only) | Written by the system on the first run. |
| `last_run` | Datetime (read-only) | |
| `last_status` | Select (read-only) | `Success`, `Replanned`, or `Failed`. |
| `last_result` | Text (read-only) | What the last run did — a row count, or the report text. |
| `consecutive_failures` | Int (read-only) | The task turns itself off at 5. |
| `last_error` | Small Text (read-only) | |

## Create a task

1. Open the **Crema Automation Task** list and create a new one.
2. Pick a trigger, a source, and an action. The form shows only the fields that apply.
3. Write the `instruction` and pick an `interface`.
4. Save the document.
5. Click **Dry Run**. Do this before you enable the task — see below.
6. Set `enabled`, then save again.

## Dry Run

**Dry Run** reads the source, writes or reuses the plan, and asks the LLM for the
rows — then stops. It creates and changes nothing. The dialog shows what the task
would write.

Use it to correct the `instruction` before the task runs on its own. A Dry Run stores
the plan it settles on, so the next real run uses the plan you looked at.

Dry Run always ignores `incremental`, so it shows records even directly after a real
run.

## Sources

### URL

The task downloads `source_url` and reduces it to text. HTML is stripped. A PDF goes
through the OCR pipeline: embedded text when the PDF has it, vision OCR when it does
not.

### Document Query

The task reads records of `source_doctype` that match `source_filters`.

The **isolation user** of the interface reads these records. A record that user
cannot read is never sent to the model. Fields above permission level 0, and password
fields, are never sent.

The query reads the record's own fields only. It does not read child tables (the
item lines of an invoice, for example), attachments, or comments.

Keep `incremental` on when the task processes changes. A run then reads only records
changed since the last run, and skips records the task itself last changed.

Turn `incremental` off when the task reports a snapshot — for example, all overdue
invoices today. Every run then sends every matching record to the model again, and
you pay for all of them again. Set `source_limit` higher than the number of matching
records: a snapshot run does not continue where the last run stopped.

A run reads at most `source_limit` records, oldest first. If more records match, the
task moves its marker to the last record it read, so the next run continues from
there. `last_result` shows how many records the run handled.

## Actions

### Upsert Records

The task creates or updates records of `target_doctype`. This is the only action that
creates records.

### Update Source Records

The task writes new values onto the records the query read. It never creates a
record. It can only change a record the query returned in the same run — a record the
model names but the query did not return is skipped.

This action needs a Document Query source. `target_doctype` is set to
`source_doctype` for you.

### Report Only

The task sends the source to the model with your `instruction` and stores the answer
in `last_result`. It writes no records at all, and needs no plan. Set `notify_to` to
also email the answer after each successful run.

## Triggers

### Schedule

The scheduler ticks every 15 minutes. A `schedule` set finer than 15 minutes still
runs once per tick, not more often.

### Document Event

The task runs when a record of `source_doctype` is created, updated, or submitted,
and it runs on that one record. This trigger needs a Document Query source.

A task never triggers itself: while a task runs, no document it writes starts another
task run.

## The plan

The first run of a writing task has no stored plan. The system writes one: an
extraction prompt, and a mapping from the source to the target doctype's fields. The
system checks every doctype name and field name in the plan against the site's own
metadata before it stores the plan. A plan that names an unknown field or doctype is
rejected.

Every run after the first reuses the stored plan. The system does not plan again,
unless a run fails. After a failure, the system plans once more, with the failure
message added, then gives up if that also fails.

Change the instruction, the source, the target, or the action, and the system drops
the stored plan. The next run writes a new one.

## Limits

- The task turns itself off after 5 runs in a row fail.
- `target_doctype` is the only doctype a task can write to. The plan cannot name a
  different one.
- `source_url`, `source_doctype`, and `source_filters` are set by a System Manager.
  The LLM never supplies a URL, a doctype, or a filter.
- A run that fails part way through keeps the records it already wrote. The retry
  skips a record whose values are already correct, so no record is written twice.
  `last_run` still moves forward.
- A task keeps only the result of its last run. For the record of each LLM call, see
  the Crema Log.

## Worked examples

Some examples name ERPNext doctypes. Crema needs only Frappe. Use the doctypes that
your site has.

### Read a web page into records

1. Create a task named `Public Holidays 2026`.
2. Keep `trigger` as `Schedule` and pick the `Daily 03:00` preset.
3. Keep `source_type` as `URL` and set `source_url` to the holidays page.
4. Set `instruction` to `Parse public holidays, update Holiday List 2026`.
5. Keep `action` as `Upsert Records` and set `target_doctype` to `Holiday List`.
6. Pick the `extraction` interface, save, then click **Dry Run**.

### Sort your own records

1. Create a task named `Triage Open Issues`.
2. Keep `trigger` as `Schedule` and pick the `Hourly` preset.
3. Set `source_type` to `Document Query` and `source_doctype` to `Issue`.
4. Click the filter table and add `status` `=` `Open`.
5. Set `instruction` to `Set priority from how urgent the description sounds`.
6. Set `action` to `Update Source Records`.
7. Pick an interface, save, then click **Dry Run**.

### Email a daily summary

1. Create a task named `Daily Issue Digest`.
2. Set `source_type` to `Document Query` and `source_doctype` to `Issue`.
3. Set `action` to `Report Only` and put your address in `notify_to`.
4. Set `instruction` to `Summarise these issues in five lines`.

With `incremental` on, the digest covers the issues that changed since the last
run — that is the point of a daily summary.

### Turn an incoming email into a lead

1. Create a task named `Email to Lead`.
2. Set `trigger` to `Document Event` and `event` to `After Insert`.
3. Set `source_type` to `Document Query` and `source_doctype` to `Communication`.
4. Click the filter table and add `sent_or_received` `=` `Received`.
5. Set `instruction` to `Take the sender's name, company, and email address`.
6. Keep `action` as `Upsert Records` and set `target_doctype` to `Lead`.
7. Pick the `extraction` interface, save, then click **Dry Run**.

The task reads the text of the message. It does not read the attachments of the
message.

### Email an overdue-invoice digest

1. Create a task named `Overdue Receivables`.
2. Keep `trigger` as `Schedule` and pick the `Daily 03:00` preset.
3. Set `source_type` to `Document Query` and `source_doctype` to `Sales Invoice`.
4. Click the filter table and add `status` `=` `Overdue`.
5. Turn `incremental` off — the report must list all overdue invoices, not only the
   ones that changed since yesterday.
6. Set `action` to `Report Only` and put the credit controller address in `notify_to`.
7. Set `instruction` to `List each customer, the amount, and how many days it is late`.

### Watch for stalled work orders

1. Create a task named `Stalled Work Orders`.
2. Keep `trigger` as `Schedule` and pick the `Daily 03:00` preset.
3. Set `source_type` to `Document Query` and `source_doctype` to `Work Order`.
4. Click the filter table and add `status` `=` `In Process`.
5. Turn `incremental` off — a work order can stall without changing.
6. Set `action` to `Report Only` and put the production planner's address in
   `notify_to`.
7. Set `instruction` to `List each work order past its planned end date. Sort by how
   far behind it is`.

### Clean up item descriptions

1. Create a task named `Clean Item Descriptions`.
2. Keep `trigger` as `Schedule` and pick the `Hourly` preset.
3. Set `source_type` to `Document Query` and `source_doctype` to `Item`.
4. Click the filter table and add `disabled` `=` `0`.
5. Set `instruction` to `Rewrite the description as one clear English sentence. Keep
   every number, size, and part code exactly as it is`.
6. Set `action` to `Update Source Records`.
7. Pick an interface, save, then click **Dry Run**.

With `incremental` on, the task cleans new and changed items only. It never re-reads
an item it cleaned itself.

### Find duplicate suppliers

1. Create a task named `Duplicate Suppliers`.
2. Keep `trigger` as `Schedule` and pick the `Weekly (Mon 03:00)` preset.
3. Set `source_type` to `Document Query` and `source_doctype` to `Supplier`.
4. Turn `incremental` off, and set `source_limit` to `200`.
5. Set `action` to `Report Only` and put the purchasing team's address in `notify_to`.
6. Set `instruction` to `Find suppliers that look like the same company entered
   twice — similar names, shared tax IDs, or shared addresses. List each pair`.

A run reads at most `source_limit` records, so this works for lists of up to 200
suppliers.

## Log retention

A daily job deletes Crema Log rows older than `Crema Settings.log_retention_days` (30
days by default — change it on the Crema Settings page).
