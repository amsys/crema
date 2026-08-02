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
| `trigger` | Select | Label **Trigger**. `Schedule` or `Document Event`. |
| `schedule_preset` | Select | Label **How Often**. A common schedule, or `Custom`. The preset rewrites `schedule` on every save — pick `Custom`, or leave the preset empty, to keep a cron expression you wrote yourself. |
| `schedule` | Data | Label **Custom Schedule (cron)**. The cron expression. The preset fills it in. |
| `event` | Select | For a Document Event trigger: `After Insert`, `On Update`, or `On Submit`. |
| `source_type` | Select | `URL` or `Document Query`. |
| `source_url` | Data | For a URL source. The only URL this task reads. Only a System Manager can set it. |
| `source_doctype` | Link (DocType) | Label **Record Type to Read**. For a Document Query source. Only a System Manager can set it. |
| `source_filters` | Code (JSON) | Label **Which Records**. Click the table to set them. |
| `source_limit` | Int | Label **Records Per Run**. Most records to read in one run. 50 by default, 200 maximum. |
| `incremental` | Check | Label **Only Changed Records**. Read only records changed since the last run. On by default. Has no effect on a Document Event run, which always reads the one record that triggered it. |
| `interface` | Autocomplete | Label **Use Case**. Required. One of the interface names (see [configure.md](configure.md)). |
| `instruction` | Text | Required. What to read from the source, and what to do with it. |
| `action` | Select | `Upsert Records`, `Update Source Records`, or `Report Only`. |
| `target_doctype` | Link (DocType) | Label **Record Type to Write**. For `Upsert Records`. The only doctype this task can write to. |
| `notify_to` | Small Text | Label **Email Report To**. For `Report Only`. Comma-separated addresses. |
| `plan_json` | Code (read-only) | Label **Raw Plan (JSON)**. Written by the system on the first run. The system replaces it when you change the plan inputs, or when the stored plan no longer validates. |
| `plan_target_doctype` | Data (read-only) | Label **Creates or Updates**. Read from the stored plan. |
| `plan_match_fields` | Data (read-only) | Label **Finds Existing Records By**. Read from the stored plan. |
| `plan_field_map` | Table (read-only) | Label **Field Mapping**. The stored plan's field map, one row per field. Read from the stored plan. |
| `plan_prompt` | Small Text (read-only) | Label **What the AI Is Asked to Pull Out**. Read from the stored plan. |
| `next_run` | Datetime (read-only) | Label **Next Run**. When the task is due next. Empty while the task is off, or when the trigger is Document Event. |
| `last_run` | Datetime (read-only) | |
| `last_status` | Select (read-only) | `Success`, `Replanned`, or `Failed`. |
| `last_result` | Text (read-only) | What the last run did — for example `3 created, 2 updated, 1 skipped`, `no new records`, or the report text. |
| `consecutive_failures` | Int (read-only) | The task turns itself off at 5. |
| `last_error` | Small Text (read-only) | |

The result of the last run shows as a coloured indicator next to the task name at the
top of the form. Open **Last Run** for the times, the result, and any error. Open
**Plan** to read the plan in a table instead of raw JSON. A task that failed three times
in a row also shows a warning across the top of the form.

## Create a task

1. Open the **Crema Automation Task** list and create a new one.
2. Pick a trigger, a source, and an action. The form shows only the fields that apply.
3. Write the `instruction` and pick an `interface`.
4. Save the document.
5. Click **Dry Run**. Do this before you enable the task — see below.
6. Set `enabled`, then save again.

The save checks more than required fields. It refuses a Crema doctype as
`source_doctype`, trial-runs `source_filters` so a broken filter fails at save time
rather than at 3am, validates every `notify_to` address, and warns — orange, without
blocking the save — when the interface's isolation user cannot read
`source_doctype`.

## Dry Run

**Dry Run** reads the source, writes or reuses the plan, and asks the LLM for the
rows — then stops. It creates and changes nothing. The dialog shows what the task
would write.

Use it to correct the `instruction` before the task runs on its own. A Dry Run stores
the plan it settles on, so the next real run uses the plan you looked at.

Dry Run always ignores `incremental`, so it shows records even directly after a real
run. It needs the `System Manager` role, and allows 20 calls per hour per client IP.

**Run Now**, beside Dry Run, queues one real run immediately. It uses the same code
path as the scheduler — always a background job, never an inline run — and needs the
`System Manager` role too.

## Sources

### URL

The task downloads `source_url` and reduces it to text. HTML is stripped. A PDF goes
through the OCR pipeline: embedded text when the PDF has it, vision OCR when it does
not.

### Document Query

The task reads records of `source_doctype` that match `source_filters`.

The **isolation user** of the interface reads these records. The query never
returns a record that user cannot read, so the model never sees it. The query also
skips fields above permission level 0 and password fields.

The query reads the record's own fields only. It does not read child tables (the
item lines of an invoice, for example), attachments, or comments.

When the interface's `enable_prompt_scan` is on, the run also scans each record on
its own before the batch goes to the model, and drops any record the scan flags.
`last_result` counts the drops — `(2 skipped by the security scan)`.

A run that finds no records stops before any LLM call, records `Success`, and
stores `no new records` in `last_result`. A quiet night costs nothing, and does not
count toward the five failures.

Keep `incremental` on when the task processes changes. A run then reads only
records changed since the last run. It skips records whose last change came from
the interface's isolation user — the task's own writes, and the writes of any other
task that shares that isolation user.

Turn `incremental` off when the task reports a snapshot — for example, all overdue
invoices today. Every run then sends every matching record to the model again, and
you pay for all of them again. Set `source_limit` higher than the number of matching
records: a snapshot run always reads the oldest matching records first, does not
continue where the last run stopped, and so never reaches records past the cap.

A run reads at most `source_limit` records, oldest first. A capped **incremental,
scheduled** run moves its watermark (`last_run`) back to the last record it read, so
the next run continues the backlog from there. Only that combination rewinds: a
rewind re-arms the schedule at the next 15-minute tick, which is the point for a
capped backlog — but it would make any other kind of run repeat, and pay, forever.

## Actions

### Upsert Records

The task creates or updates records of `target_doctype`. This is the only action that
creates records.

### Update Source Records

The task writes new values onto the records the query read. It never creates a
record. It can only change a record the query returned in the same run — the system
skips a record the model names but the query did not return.

This action needs a Document Query source. The system sets `target_doctype` to
`source_doctype` for you.

### Report Only

The task sends the source to the model with your `instruction` and stores the answer
in `last_result`. It writes no records at all, and needs no plan. Set `notify_to` to
also email the answer after each successful run.

## Triggers

### Schedule

The scheduler ticks every 15 minutes. A `schedule` set finer than 15 minutes still
runs once per tick, not more often. The tick queues each due task once — it never
double-queues a task that is already waiting or running.

### Document Event

The task runs when a record of `source_doctype` is created, updated, or submitted,
and it runs on that one record — `incremental` has no effect here. This trigger
needs a Document Query source.

A task never triggers itself: while a task runs, no document it writes starts another
task run.

## The plan

The first run of a writing task has no stored plan. The system writes one: an
extraction prompt, and a mapping from the source to the target doctype's fields. The
mapping may include one child table — the item lines of an invoice, for example —
with one child row per extracted row.

A plan is parameters, never code. The system accepts only the keys it knows, so a
plan cannot smuggle in a script. It also checks every doctype name and field name in
the plan against the site's own metadata before it stores the plan. The system
rejects a plan that names an unknown key, field, or doctype.

Every run after the first reuses the stored plan. The system does not plan again,
unless a run fails. After a failure, the system plans once more, with the failure
message added, then gives up if that also fails.

Change the instruction, the source, the target, or the action, and the system drops
the stored plan. The system also drops a stored plan that no longer validates —
after a doctype change, for example. The next run writes a new one.

## Limits

- The task turns itself off after 5 runs in a row fail.
- `target_doctype` is the only doctype a task can write to. The plan cannot name a
  different one.
- Only a System Manager sets `source_url`, `source_doctype`, and `source_filters`.
  The LLM never supplies a URL, a doctype, or a filter.
- A run that fails part way through keeps the records it already wrote. For a record
  without child-table rows, the retry skips it when its values are already correct,
  so the task does not write it twice. The task writes a record with child-table
  rows again on every retry. `last_run` still moves forward.
- The system skips an extracted row whose match value is empty, or is a list or an
  object — an extracted value can never smuggle a filter operator into the record
  lookup. On an update, extracted values can never change which record the task
  writes to: the system strips `name` from the values before the update.
- Content caps: the planner sees the first 8,000 characters of the source, and the
  extractor the first 60,000. `last_result` holds at most 20,000 characters, and
  `last_error` 2,000.
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
