# Automate with Crema

A **Crema Automation Task** reads a source, sends it to an LLM, and does something
with the result — on a schedule, or when a document changes. Each task runs four
steps in order: SOURCE, PLAN, EXTRACT, WRITE. A [File Query](#file-query) source is the
exception: it skips PLAN and EXTRACT, mapping each file onto the target doctype
directly.

Build a task from three choices:

| Choice | Options |
|---|---|
| **Trigger** — when it runs | `Schedule` (a cron expression), `Once` (a date and time), `Document Event`, `Incoming Email`, or `Webhook` |
| **Sources** — what it reads | One or more rows: `URL`, `Document Query` (records on this site), or `File Query` (uploaded files, read into records). A task can mix `URL` and `Document Query`; a `File Query` row must stand alone. A Webhook task can also read what the caller sends. |
| **Action** — what it does | `Create or Update Records`, `Update the Records It Read`, `Propose Only`, or `No Changes` |

Any task can also email what it did. That is not one of the three choices — put an
address in **Email Report To** and it applies to whichever action you picked.

## Crema Automation Task fields

| Field | Type | Notes |
|---|---|---|
| `task_name` | Data | Required. Unique. |
| `enabled` | Check | Off by default. |
| `trigger` | Select | Label **Trigger**. `Schedule`, `Once`, `Document Event`, `Incoming Email`, or `Webhook`. |
| `schedule_preset` | Select | Label **How Often**. A common schedule, or `Custom`. The preset rewrites `schedule` on every save — pick `Custom`, or leave the preset empty, to keep a cron expression you wrote yourself. |
| `schedule` | Data | Label **Custom Schedule (cron)**. The cron expression. The preset fills it in. |
| `run_at` | Datetime | Label **Run At**. Required for a Once trigger. The one time the task runs. Set it to a new time in the future to run the task again. |
| `event` | Select | For a Document Event trigger: `After Insert`, `On Update`, or `On Submit`. An Incoming Email trigger sets it to `On Update` for you. |
| `webhook_endpoint` | Data (read-only) | Label **Endpoint**. For a Webhook trigger. The address to call. |
| `read_webhook_payload` | Check | Label **Use Webhook Data**. On by default. For a Webhook trigger. Reads what the caller sends as one more source. |
| `sources` | Table (Crema Automation Source) | Label **Sources**. Required, except for a Webhook task that uses webhook data. One row per thing this task reads. See the field list below. |
| `on_source_error` | Check | Label **Stop if a Source Fails**. Off by default. Shown when a task has more than one source. |
| `interface` | Select | Label **AI Profile**. Required. A dropdown of use cases, shown by name (see [configure.md](configure.md)). `Advanced OCR`, `View`, `Transform`, `OCR` and `Transcribe` are not offered — a task must never run as any of them. |
| `run_as` | Link (User) | Label **Runs As**. The account this task acts as. Empty uses the account set for the AI profile. Not `Administrator`, not a System Manager, not a disabled user. |
| `instruction` | Text | Required. What to read from the source, and what to do with it. |
| `action` | Select | `Create or Update Records`, `Update the Records It Read`, `Propose Only`, or `No Changes`. |
| `target_doctype` | Link (DocType) | Label **Record Type to Write**. For `Create or Update Records` and `Propose Only`. The only doctype this task can write to (or propose writing to). |
| `match_on` | Data | Label **Match Records On**. Comma-separated fieldname(s) on `target_doctype`. Required for a `File Query` source — see [File Query](#file-query) below. Unused, and cleared, by the other actions. |
| `confidence_floor` | Float | Label **Lowest Confidence to Accept**. 0 by default. Applies only to a `File Query` source — see [Propose Only](#propose-only). A file read below this confidence is skipped, whichever action the task uses. |
| `notify_to` | Small Text | Label **Email Report To**. Comma-separated addresses. Works with every action. |
| `plan_json` | Code (read-only) | Label **Raw Plan (JSON)**. Written by the system on the first run. The system replaces it when you change the plan inputs, or when the stored plan no longer validates. |
| `plan_target_doctype` | Data (read-only) | Label **Creates or Updates**. Read from the stored plan. |
| `plan_match_fields` | Data (read-only) | Label **Finds Existing Records By**. Read from the stored plan. |
| `plan_field_map` | Table (read-only) | Label **Field Mapping**. The stored plan's field map, one row per field. Read from the stored plan. |
| `plan_prompt` | Small Text (read-only) | Label **What the AI Is Asked to Pull Out**. Read from the stored plan. |
| `next_run` | Datetime (read-only) | Label **Next Run**. When the task is due next. Empty while the task is off, after a Once task has run, or when the trigger is neither Schedule nor Once. |
| `last_run` | Datetime (read-only) | |
| `last_status` | Select (read-only) | `Success`, `Replanned`, or `Failed`. |
| `last_result` | Text (read-only) | What the last run did — for example `3 created, 2 updated, 1 skipped`, `no new records`, or the report text. |
| `last_written_json` | Code (read-only) | The records the last run created and updated. Feeds the **Undo Last Run** button — see [Undo a run](#undo-a-run). |
| `consecutive_failures` | Int (read-only) | The task turns itself off at 5. |
| `last_error` | Small Text (read-only) | |

## Crema Automation Source fields

One row per source, in the **Sources** table. The grid shows five columns — **Type**,
**What** (the record type or the address, with the number of filters after it), **Per
Run**, **Changed** and **Files** — and the rest opens with the row's edit button. Change
**Per Run**, **Changed** and **Files** in the grid itself.

The rows are read in the order shown, but the order changes nothing: each row is read on
its own and the results are joined. The grid is deliberately not numbered.

**Per Run** and **Changed** apply to a Document Query or a File Query. **Files**
(attachments) and **Lines** (child table rows) apply to a Document Query only — a File
Query already reads files, so it has nothing of its own to attach or read as lines. A
URL row shows all four empty, and clears them when you save.

| Field | Type | Notes |
|---|---|---|
| `source_type` | Select | Label **Type**. `URL`, `Document Query`, or `File Query`. |
| `source_url` | Data | For a URL source. The only address this source reads. Only a System Manager can set it. Must resolve to a public address — a private, loopback, or link-local address (including a redirect to one) is refused when the source runs. |
| `source_doctype` | Link (DocType) | Label **Record Type to Read**. For a Document Query source. Only a System Manager can set it. A File Query source always reads `File` — this is filled in for you and not shown. |
| `source_filters` | Code (JSON) | Label **Which Records**. Click the table to set them. For a File Query, filters narrow which files — for example `attached_to_doctype`, `is_private`, or `file_name`. |
| `source_limit` | Int | Label **Per Run**. Most records (or files) to read in one run. 50 by default for a Document Query, 200 maximum; 10 by default for a File Query, 50 maximum — each file costs at least two AI calls, against one per record for a Document Query. |
| `incremental` | Check | Label **Changed**. Read only records (or files) changed since this source was last read. On by default. Has no effect on a Document Event run, which always reads the one record (or file) that triggered it. |
| `read_attachments` | Check | Label **Files**. Also read the files attached to each record. See [Attachments](#attachments) below. Not available on a File Query source. |
| `read_children` | Check | Label **Lines**. Also read each record's line items — invoice items, or BOM components, for example. Up to 20 lines for each record. Not available on a File Query source. |
| `last_read` | Datetime (read-only) | Label **Read Up To**. How far this source has been read. Clear it to read everything again. |
| `source_label` | Data (read-only) | Label **What**. Filled in for you: the record type, the address, or `Files`, and the number of filters (`Communication · 2 filters`). A grid column only — it does not appear when you open the row. |

The result of the last run shows as a coloured indicator next to the task name at the
top of the form. Open **Last Run** for the times, the result, and any error. Open
**Plan** to read the plan in a table instead of raw JSON. A task that failed three times
in a row also shows a warning across the top of the form.

## Crema Proposal fields

One row per record a `Propose Only` task parked — see [Propose Only](#propose-only).
Never edited by hand; a System Manager only reads, approves, or discards a row.

| Field | Type | Notes |
|---|---|---|
| `task` | Link (Crema Automation Task) | The task that parked this row. |
| `status` | Select | `Pending`, `Approved`, or `Discarded`. |
| `target_doctype` | Link (DocType) | Label **Record Type**. What this row would create or change once approved. |
| `confidence` | Float | The OCR pass's confidence in the file this row was read from, 0 to 1. Empty for a row read from text rather than a file. |
| `diff_preview` | HTML (read-only) | Label **Proposed Write**. The fields approving this row writes, as a table. Built on the form from `payload_json` and `mapping_json`; it is not stored. |
| `outcome` | Small Text (read-only) | What approving this row did — the same counts an unattended run would have recorded. |
| `created_json` | Code (read-only) | The record approving this row created. Feeds the **Undo** button on an Approved row — see [Undo a run](#undo-a-run). Empty when approving only updated an existing record. |
| `fingerprint` | Data (read-only) | Identifies this row so a repeated run finds it instead of filing a duplicate. |
| `payload_json` | Code (read-only) | The extracted row, unchanged, as the writer would receive it. |
| `mapping_json` | Code (read-only) | How the payload maps onto the record type. |

## Create a task

1. Open the **Crema Automation Task** list and create a new one.
2. Pick a trigger, a source, and an action. The form shows only the fields that apply.
3. Write the `instruction` and pick an **AI Profile**.
4. Save the document.
5. Click **Dry Run**. Do this before you enable the task — see below.
6. Set `enabled`, then save again.

The save checks more than required fields. It needs at least one source (a Webhook task
may have none while **Use Webhook Data** is on), refuses a Crema doctype as `source_doctype`, trial-runs each row's
`source_filters` so a broken filter fails at save time rather than at 3am, validates every
`notify_to` address, refuses a **Runs As** account that would dissolve the sandbox, and
warns — orange, without blocking the save — when the account the task runs as cannot read
one of the record types the task is pointed at.

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

## Undo a run

**Undo Last Run** deletes every record the task's last run created. It leaves every
record the run updated alone, for a person to review — the form's dialog links to both
lists before you confirm. Only the most recent run is reversible: `last_written_json`
holds one run's list, replaced by the next run. The button is hidden once there is
nothing left to undo.

A record another document links to is refused, not force-deleted — the same link check
`frappe.delete_doc` always applies. A record that fails to delete stays listed, so you
can clear the reason and undo again.

`Update the Records It Read` never creates a record, so a run of that action has nothing
to undo. `Propose Only` writes nothing until a person approves a proposal — see
[Propose Only](#propose-only) below for its own Undo.

## Sources

A task reads every row of its **Sources** table on each run, in order, and joins the
text into one block per source, headed by `=== Source: <what> ===`. The model reads
them together, so one task can compare a downloaded price list against records on this
site.

A Webhook task with **Use Webhook Data** on counts what the caller sends as one more
source. Switch the box off and the task ignores it, and reads only the rows.

Each source gets an equal share of the 60,000-character content limit — two sources
get 30,000 characters each. A source that reads more than its share is cut short. The
webhook data takes a share of its own, and only while the box is on.

When a source cannot be read — a web address that is down, a query that errors — the
task follows **Stop if a Source Fails**:

* Off (default) runs with the sources that did answer, and adds the failure to
  `last_result`.
* On stops the run. Nothing is written.

A run where *every* source failed is `Failed` either way.

You choose the sources. The model never adds one, and never changes what one reads.

### URL

The source downloads `source_url` and reduces it to text. HTML is stripped. A PDF goes
through the OCR pipeline: embedded text when the PDF has it, vision OCR when it does
not.

The download refuses to reach a private, loopback, or link-local address, on the start
URL and on every redirect hop — see [docs/security.md](security.md#known-limits).

### Document Query

The source reads records of `source_doctype` that match `source_filters`.

The account in **Runs As** reads these records — the task's own if it has one, otherwise
the account set for the use case. The query never returns a record that account cannot
read, so the model never sees it. The query also skips fields above permission level 0 and
password fields.

The query reads the record's own fields. It does not read comments. Switch on
**Files** to also read the files attached to each record. Switch on **Lines** to also
read each record's line items — the item lines of an invoice, or the components of a
BOM, for example. Crema reads up to 20 lines for each record, split across every
table on the doctype if it has more than one; a record that lost lines to this limit
shows in `last_result` as `(1 record(s) had table lines left out)`.

When the Text Scan row applies to the task's use case, the run also scans each
record on its own before the batch goes to the model, and drops any record the scan
flags. `last_result` counts the drops — `(2 skipped by the security scan)`.

When a Hide Personal Information row applies to the task's use case, the records
read also feed masking: each record's title, the names and titles of the records it
links to, and its phone and email fields become exact placeholders for that run.
Several Document Query sources add their terms together. See
[security.md](security.md#masking-experimental).

A run whose sources produce no text at all stops before any LLM call, records
`Success`, and stores `no new records` in `last_result`. A quiet night costs nothing, and does not
count toward the five failures.

Keep **Changed** on when the source feeds a task that processes changes. A run then
reads only records changed since **Read Up To** (`last_read`), the watermark this
source keeps for itself. It skips records whose last change came from
the account the task runs as — the task's own writes, and the writes of any other task
that runs as the same account.

Turn `incremental` off when the task reports a snapshot — for example, all overdue
invoices today. Every run then sends every matching record to the model again, and
you pay for all of them again. Set `source_limit` higher than the number of matching
records: a snapshot run always reads the oldest matching records first, does not
continue where the last run stopped, and so never reaches records past the cap.

A run reads at most `source_limit` records, oldest first. When a run fills that cap,
more records may remain: the watermark stops at the last record read, and the next run
continues the backlog from there. An uncapped run moves the watermark to the moment the
run started, so a record changed while the run was still working is read next time and
not skipped. A failed run moves no watermark at all — it reads the same records again.

`last_run` is not a watermark. It is only the point the schedule counts from, and
nothing moves it backwards.

### File Query

The source reads files that match `source_filters` — for example every unattached PDF
uploaded in the last day, or every file attached to `Sales Invoice`. Each matching file
goes through the same file-to-records pipeline as a robot-button import on a form: OCR
(embedded text when a PDF has it, vision OCR when it does not), then a mapping onto
`target_doctype`'s fields. The model decides how many records one file describes — one
invoice is one record, a statement listing several invoices is several.

A File Query source skips PLAN and EXTRACT — there is no shared plan to write, because
each file is mapped against `target_doctype`'s own fields directly. This is also why
`match_on` exists: with no plan, nothing else tells the task which field (or fields)
identify "the same record" so that reading an invoice a second time updates it instead
of importing a duplicate. Set **Match Records On** to a fieldname that will hold a
stable value from the file — an invoice number, an email address — comma-separated if
more than one field is needed together.

A File Query source has two things the others don't:

* **It cannot share a task with any other source.** A run either reads files into
  records, or reads text — mixing the two has no single result to act on.
* **It only supports `Create or Update Records` or `Propose Only`.** `Update the
  Records It Read` would mean writing back to `File` itself; `No Changes` wants text
  to summarise, which this path never produces.

A file Crema cannot read — anything that is not a PDF or a picture — is skipped and
noted in `last_result`, the same as an unreadable attachment. A file whose text trips
the scan is skipped and noted too, rather than failing the whole run; its
siblings still import. See [security.md](security.md) for what the account in
**Runs As** needs to be able to list files it does not own.

Confidence is a File Query source's own reading, not a model self-assessment: it is
the same score the OCR pass gives every scanned document (see
[Propose Only](#propose-only)). Set **Lowest Confidence to Accept** above 0 and a file
read below it is skipped and noted, the same way an unreadable file is — before it
is written, or proposed, either way.

### Attachments

Switch on **Files** and the source also reads the files attached to each record it
reads. Crema reads PDFs and pictures through the OCR pipeline, and adds the text under the
record it belongs to:

```
-- attachment invoice-4471.pdf on 9gdeljd98h --
ACME Ltd   Invoice 4471   Due 2026-09-01 ...
```

* Files of any other kind are skipped. Crema cannot read a spreadsheet or a Word file.
* At most five files per record.
* Each file is one OCR call: it is billed, it counts against the monthly limit, and it
  writes its own row in the Crema Log. Ten records with two files each is twenty calls.
* A file Crema cannot read is noted in `last_result`; the rest of the run continues.
* Crema lists the files with the same account that read the record, so the account needs
  read access to **File**. See [security.md](security.md) for what this fence does and
  does not cover.

Attachments are not an email feature. Every record on the site can have files attached —
by the **Attach** button, by dragging a file onto the form, or by an incoming email — and
this reads all of them the same way. See
[Turn an emailed invoice into a record](#turn-an-emailed-invoice-into-a-record) for the
email case.

## Actions

### Create or Update Records

The task creates or updates records of `target_doctype`. This is the only action that
writes a record without a person looking at it first — see [Propose
Only](#propose-only) for the same result held back for approval.

### Update the Records It Read

The task writes new values onto the records the query read. It never creates a
record. It can only change a record the query returned in the same run — the system
skips a record the model names but the query did not return.

This action needs exactly one Document Query source — it writes back to the records it
read, and cannot do that for two record types at once. The system sets `target_doctype`
to that source's record type for you. A URL source alongside it is fine: it is reference
material, and the task still only writes to records the query returned.

### Propose Only

The task works out what `Create or Update Records` would have written, and parks it as
a **Crema Proposal** instead of writing it — nothing is created or changed until a
person approves it. This is the one action every trigger, including an unattended
schedule, can run without writing anything on its own.

Each proposal carries the record type, the field values it would set, and — for a File
Query source — the confidence the OCR pass had in the file it came from (see
[File Query](#file-query) above; a plan-based source has no equivalent measurement).
Open the **Proposals** button on the task, or the **Crema Proposal** list, to review
them: **Approve** writes the record the same way `Create or Update Records` would have,
and stamps what it did; **Discard** removes it from the queue and writes nothing. An
Approved row also shows **Undo**: it deletes the record approving it created and returns
the row to Pending, so it can be approved again or discarded. All three work on one row
from its form, or on several at once from the list view's Actions menu.

The proposal form shows **Proposed Write** at the top: the fields that approving the row
writes, as a table. **Approve** from the list view first shows the same table for every
row you selected, and writes nothing until you confirm.

A run that reads the same source again does not file a duplicate proposal for content
it already parked — approved, discarded, or still pending. This is what makes it safe
to leave a `Propose Only` task on the same schedule as any other task: a crash or a
retry finds its earlier proposal instead of piling up copies.

### No Changes

The task sends the source to the model with your `instruction` and stores the answer
in `last_result`. It writes no records at all, and needs no plan.

### Email a report

**Email Report To** is not an action. Put one or more addresses there and Crema emails the
result after each successful run, whichever action the task uses:

* `No Changes` emails the model's answer.
* `Create or Update Records` and `Update the Records It Read` email what the run did —
  `3 created, 2 updated, 1 skipped`.
* `Propose Only` emails how many rows it parked — `3 proposed, 1 already proposed`.

A failed run sends nothing. A mail that cannot be sent is logged and never turns a
successful run into a failed one.

## Triggers

### Schedule

The scheduler ticks every 15 minutes. A `schedule` set finer than 15 minutes still
runs once per tick, not more often. The tick queues each due task once — it never
double-queues a task that is already waiting or running.

### Once

The task runs one time, at the date and time in **Run At**. Use it for a job with a
day of its own — an import the night before a cutover, a clean-up after a migration.

The scheduler ticks every 15 minutes, so the task starts at that time or shortly after
it. It runs once and stays on: **Next Run** goes empty, and nothing runs it again.

To run it again, set **Run At** to a new time in the future. The task is due again as
soon as that time passes.

### Document Event

The task runs when a record of any Document Query source is created, updated, or
submitted. That source then reads only the record that changed — `incremental` has no
effect here. Every other source is still read in full, so a task can check the changed
record against a reference URL. This trigger needs at least one Document Query source.

A task never triggers itself: while a task runs, no document it writes starts another
task run.

### Incoming Email

The task runs when a message arrives in a Frappe inbox. This is a Document Event trigger
with the parts filled in for you: Crema sets `event` to `On Update` and adds one Document
Query source on **Communication**, filtered to `sent_or_received` `=` `Received`, with
**Files** on and **Per Run** set to 5. The source row appears as soon as you select the
trigger.

Crema does not collect mail. Frappe does that already: an **Email Account** with
**Enable Incoming** on writes one **Communication** per message, and attaches each file of
the message to that Communication. This trigger watches those Communications.

The seeded source row is an ordinary row. Open it and add `has_attachment` `=` `1` to skip
messages that carry no file, or `email_account` to watch one inbox out of several. Delete
it and Crema adds it back on the next save. Change the trigger to something else and the
row stays as it is.

`On Update`, not `After Insert`, is why the trigger exists: Frappe saves the message first
and attaches the files a moment later, so a task triggered on the insert finds no files.

### Webhook

The task runs only when another system calls it. Nothing on this site starts it: the
scheduler skips it, and no document event reaches it.

Send a POST to the address in **Endpoint**:

```
POST /api/method/crema.api.trigger_automation
Authorization: token <api_key>:<api_secret>
Content-Type: application/x-www-form-urlencoded

task=Invoice%20from%20portal&payload=%7B%22order%22%3A%2041%7D
```

The reply is the id of the queued job.

* **Sign in with a Frappe API key and secret.** Make an ordinary user for the caller and
  give it a role that can read **Crema Automation Task**. It does not need — and should
  not have — the System Manager role.
* Crema refuses the call unless the task's trigger is `Webhook` and the task is on. A
  scheduled task cannot be started this way by anyone.
* `payload` is optional text, at most 20,000 characters. While **Use Webhook Data** is on,
  Crema reads it as one more source, under the heading `=== Source: webhook payload ===`,
  and the text scan checks it like any other source. Switch the box off and Crema drops the
  payload — the task still runs, and reads only its sources.
* A Webhook task that uses webhook data may have no sources at all, and read only what the
  caller sends.
* At most 60 calls per hour per address.

### Sign the call

**Webhook Secret**, on the same task, is optional. Set it and every call must also carry
`timestamp` (Unix seconds) and `signature`:

```
signature = hex(HMAC-SHA256("{timestamp}.{task}.{payload}", key=webhook_secret))
```

`payload` is the exact text sent, or an empty string if the field is absent — sign it
before any truncation on the crema side. Crema refuses a call that is unsigned, whose
timestamp is more than 5 minutes old or in the future, whose signature does not match, or
whose signature was already used once. A task with no Webhook Secret set accepts a call on
the Frappe API key and secret alone, exactly as before this existed.

```
POST /api/method/crema.api.trigger_automation
Authorization: token <api_key>:<api_secret>
Content-Type: application/x-www-form-urlencoded

task=Invoice%20from%20portal&payload=%7B%22order%22%3A%2041%7D&timestamp=1756400000&signature=9f2c...
```

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

Change the instruction, the target, the action, or which sources the task reads, and
the system drops the stored plan. Changing a source's filters or its records-per-run
does not: the shape of what it reads is the same, only how much of it. The system also drops a stored plan that no longer validates —
after a doctype change, for example. The next run writes a new one.

## Limits

- The task turns itself off after 5 runs in a row fail.
- `target_doctype` is the only doctype a task can write to. The plan cannot name a
  different one.
- A record type on Crema Settings' Blocked Doctypes table can never be a task's
  target, or a plan's child-table target — checked at save time and on every run. See
  [docs/security.md](security.md#a-site-wide-write-block).
- Only a System Manager sets the source rows. The LLM never supplies a URL, a doctype,
  or a filter, and never adds a source.
- A run that fails part way through keeps the records it already wrote. For a record
  without child-table rows, the retry skips it when its values are already correct,
  so the task does not write it twice. The task writes a record with child-table
  rows again on every retry. `last_run` still moves forward.
- The system skips an extracted row whose match value is empty, or is a list or an
  object — an extracted value can never smuggle a filter operator into the record
  lookup. On an update, extracted values can never change which record the task
  writes to: the system strips `name` from the values before the update.
- Content caps: the extractor sees at most 60,000 characters, shared equally between
  the sources (a webhook payload counts as one more source while **Use Webhook Data** is
  on), and the planner the first
  8,000 of the joined text. A webhook payload holds at most 20,000 characters.
  `last_result` holds at most 20,000 characters, and `last_error` 2,000.
- At most 5 attachments per record, PDFs and pictures only. Each one is a separate,
  billed OCR call.
- A File Query source cannot share a task with any other source, and only supports
  `Create or Update Records`. It reads at most `source_limit` files per run — 10 by
  default, 50 maximum — and each file is two billed calls (OCR, then extraction).
- A task keeps only the result of its last run. For the record of each LLM call, see
  the Crema Log.

## Worked examples

Some examples name ERPNext doctypes. Crema needs only Frappe. Use the doctypes that
your site has.

### Read a web page into records

1. Create a task named `Public Holidays 2026`.
2. Keep `trigger` as `Schedule` and pick the `Daily 03:00` preset.
3. Add a source row, keep its `source_type` as `URL`, and set `source_url` to the
   holidays page.
4. Set `instruction` to `Parse public holidays, update Holiday List 2026`.
5. Keep `action` as `Create or Update Records` and set `target_doctype` to `Holiday List`.
6. Pick the `extraction` AI profile, save, then click **Dry Run**.

### Sort your own records

1. Create a task named `Triage Open Issues`.
2. Keep `trigger` as `Schedule` and pick the `Hourly` preset.
3. Add a source row, set its `source_type` to `Document Query`, and set
   `source_doctype` to `Issue`.
4. Click the filter table and add `status` `=` `Open`.
5. Set `instruction` to `Set priority from how urgent the description sounds`.
6. Set `action` to `Update the Records It Read`.
7. Pick an AI profile, save, then click **Dry Run**.

### Email a daily summary

1. Create a task named `Daily Issue Digest`.
2. Add a source row, set its `source_type` to `Document Query`, and set
   `source_doctype` to `Issue`.
3. Set `action` to `No Changes` and put your address in `notify_to`.
4. Set `instruction` to `Summarise these issues in five lines`.

With `incremental` on, the digest covers the issues that changed since the last
run — that is the point of a daily summary.

### Turn an incoming email into a lead

1. Create a task named `Email to Lead`.
2. Set `trigger` to `Incoming Email`. Crema adds the Communication source row for you.
3. Set `instruction` to `Take the sender's name, company, and email address`.
4. Keep `action` as `Create or Update Records` and set `target_doctype` to `Lead`.
5. Pick the `extraction` AI profile, save, then click **Dry Run**.

The task reads the text of the message. Switch on **Files** to also read the files
that came with it — see the next example.

### Turn an emailed invoice into a record

A supplier emails an invoice as a PDF. This reads the PDF and makes a record of it.

Crema does not collect mail. Frappe does that already: an **Email Account** with
**Enable Incoming** on writes one **Communication** per message, and attaches each file of
the message to that Communication. This task watches those Communications.

1. Set up the Email Account for the inbox the invoices arrive in.
2. Create a task named `Invoice from email`.
3. Set `trigger` to `Incoming Email`. Crema adds the Communication source row, switches
   **Files** on, and sets **Per Run** to 5 — every file is a billed OCR call.
4. Save, then open the source row and add `has_attachment` `=` `1` to the filters, so the
   task skips messages that carry no file. Add `email_account` too if more than one inbox
   is in use.
5. Set `instruction` to `Take the supplier, the invoice number, the invoice date, the due
   date, the currency, and the total from each invoice. Ignore anything that is not an
   invoice.`
6. Set `action` to `Create or Update Records` and `target_doctype` to `Purchase Invoice`.
7. Pick the `extraction` AI profile, save, then click **Dry Run**.

Set **Runs As** to an account that may read `Communication` and create the record type
this task writes to, and nothing more.

Crema installs this task for you, switched off, named **Example — invoice from email**,
with `action` set to `No Changes` so it writes nothing until you point it at a record
type. Edit it or delete it — Crema seeds it once and never puts it back.

### Turn uploaded invoices into records

Anyone attaches a supplier invoice to a **File** in the desk — the Attach button, a
drag-and-drop, or an integration that uploads files directly — and this reads it into a
record. Unlike [Turn an emailed invoice into a record](#turn-an-emailed-invoice-into-a-record),
nothing needs to arrive by mail first.

1. Create a task named `Invoices from Uploads`.
2. Keep `trigger` as `Schedule` and pick the `Hourly` preset.
3. Add a source row and set its `source_type` to `File Query`.
4. Click the filter table and add `is_private` `=` `0` (or narrow to
   `attached_to_doctype` if invoices always land on a particular doctype).
5. Set `instruction` to `Take the supplier, the invoice number, the invoice date, the
   due date, the currency, and the total from each invoice`.
6. Keep `action` as `Create or Update Records`, set `target_doctype` to
   `Purchase Invoice`, and set **Match Records On** to a field that will hold a stable
   value — `bill_no`, for example.
7. Pick the `extraction` AI profile, save, then click **Dry Run**.

A file read a second time — reprocessed after a correction, or uploaded twice by
mistake — updates the same record instead of creating a duplicate, because of the
field set in **Match Records On**.

### Email an overdue-invoice digest

1. Create a task named `Overdue Receivables`.
2. Keep `trigger` as `Schedule` and pick the `Daily 03:00` preset.
3. Add a source row, set its `source_type` to `Document Query`, and set
   `source_doctype` to `Sales Invoice`.
4. Click the filter table and add `status` `=` `Overdue`.
5. Turn `incremental` off — the report must list all overdue invoices, not only the
   ones that changed since yesterday.
6. Set `action` to `No Changes` and put the credit controller address in `notify_to`.
7. Set `instruction` to `List each customer, the amount, and how many days it is late`.

### Watch for stalled work orders

1. Create a task named `Stalled Work Orders`.
2. Keep `trigger` as `Schedule` and pick the `Daily 03:00` preset.
3. Add a source row, set its `source_type` to `Document Query`, and set
   `source_doctype` to `Work Order`.
4. Click the filter table and add `status` `=` `In Process`.
5. Turn `incremental` off — a work order can stall without changing.
6. Set `action` to `No Changes` and put the production planner's address in
   `notify_to`.
7. Set `instruction` to `List each work order past its planned end date. Sort by how
   far behind it is`.

### Clean up item descriptions

1. Create a task named `Clean Item Descriptions`.
2. Keep `trigger` as `Schedule` and pick the `Hourly` preset.
3. Add a source row, set its `source_type` to `Document Query`, and set
   `source_doctype` to `Item`.
4. Click the filter table and add `disabled` `=` `0`.
5. Set `instruction` to `Rewrite the description as one clear English sentence. Keep
   every number, size, and part code exactly as it is`.
6. Set `action` to `Update the Records It Read`.
7. Pick an AI profile, save, then click **Dry Run**.

With `incremental` on, the task cleans new and changed items only. It never re-reads
an item it cleaned itself.

### Check your records against a published list

1. Create a task named `Check Prices Against Supplier List`.
2. Keep `trigger` as `Schedule` and pick the `Daily 03:00` preset.
3. Add a source row, set its `source_type` to `Document Query`, and set
   `source_doctype` to `Item Price`.
4. Add a second source row, keep its `source_type` as `URL`, and set `source_url` to
   the supplier's published price list.
5. Set `action` to `No Changes` and put the buyer's address in `notify_to`.
6. Set `instruction` to `List every item whose price differs from the supplier list.
   Give both prices`.

Both sources reach the model in one request, each under its own heading, so the model
can compare them. Leave **Stop if a Source Fails** off and the task still reports on the
day the supplier's page is down — it says so in the result.

### Find duplicate suppliers

1. Create a task named `Duplicate Suppliers`.
2. Keep `trigger` as `Schedule` and pick the `Weekly (Mon 03:00)` preset.
3. Add a source row, set its `source_type` to `Document Query`, and set
   `source_doctype` to `Supplier`.
4. Turn `incremental` off, and set `source_limit` to `200`.
5. Set `action` to `No Changes` and put the purchasing team's address in `notify_to`.
6. Set `instruction` to `Find suppliers that look like the same company entered
   twice — similar names, shared tax IDs, or shared addresses. List each pair`.

A run reads at most `source_limit` records, so this works for lists of up to 200
suppliers.

## Log retention

A daily job deletes Crema Log rows older than `Crema Settings.log_retention_days` (30
days by default — change it on the Crema Settings page).
