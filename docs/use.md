# Use Crema

This page covers the calls a developer makes: from Python, or over HTTP.

## `ask` — call an interface

```python
def ask(interface: str, prompt: str | None = None, *, context: str | None = None,
        files: list[str | tuple[bytes, str]] | None = None, response_format: dict | None = None,
        cache_ttl: int | None = None, history: list[dict] | None = None) -> str
```

Send `prompt` to the named `interface`. The call returns the model's text response.

- `context` adds background text. The system scans `context` and `prompt` together,
  because an attack can split across the two fields.
- `files` is a list of File URLs, or `(bytes, mime)` pairs for content you already hold
  in memory and have permission-checked yourself (a bot photo with no File behind it,
  for example). For each File URL, the system checks read permission as the
  interface's isolation user, then attaches the file: an image goes as vision input,
  a scanned PDF goes as rendered page images, and a text PDF goes as its extracted
  text. The system silently skips a File it cannot read, and any type that is not a
  PDF or an image — the call still runs, without that file. The system uses a
  `(bytes, mime)` pair as-is.
- `response_format` requests a structured reply, for example
  `{"type": "json_object"}`.
- `cache_ttl` overrides the interface's cache setting for this one call.
- `history` adds prior turns — a list of `{"role": "user"|"assistant", "content": str}`
  dicts — between the interface's system prompt and this call's prompt. Crema keeps no
  conversation state of its own; pass the same list back on the next turn to continue
  it. The system scans the whole history together with `context` and `prompt`.

Example:

```python
from crema import ask
ask("Fix grammar: helo wrld")
```

Call `ask` with just a prompt and it runs the `simple` interface. `ask.simple("...")`
and `ask("simple", "...")` run the same code. Every interface name is available this
way: `ask.translation(...)`, `ask.complex(...)`, and so on.

## `ask_json` — call an interface and parse the JSON reply

```python
def ask_json(interface: str, prompt: str, **kw) -> dict
```

Same as `ask`, and also parses the reply as JSON. Unless you pass your own
`response_format`, it asks the provider for a JSON object
(`response_format={"type": "json_object"}`). Use this when the interface's prompt
asks for a JSON object.

## `ocr` — read text from a file

```python
def ocr(file: str | bytes, instruction: str | None = None) -> dict
# returns {"text": str, "confidence": float, "escalated": bool}
```

Read `file` — a File URL or raw bytes. `file` may be an image, a scanned PDF, or a
text PDF. The system picks the right method for each case. If the `ocr` interface
reports low confidence, the system retries once with `advanced_ocr`. `escalated`
tells you the retry ran; the call returns whichever attempt reports the higher
confidence, so `escalated: true` can come back with the first attempt's text. The
system skips the retry in three cases: `advanced_ocr` does not resolve to a
provider, `advanced_ocr` resolves to the same provider and model as `ocr` (the
fast-path default — see [configure.md](configure.md#procedure-b--set-the-defaults-the-fast-path)),
or `advanced_ocr`'s monthly budget is already spent. Use `instruction` to state
layout hints, for example "the invoice number sits in the top-right box".

## `transform` — propose a change to an existing document

```python
def transform(doctype: str, name: str, instruction: str,
              interface: str = "transform") -> dict
# returns {"set": {...}, "child_set": {...}, "reason": str}
```

Read the document, and propose new field values from `instruction`. This call never
writes to the document. Apply the result yourself, under your own permissions:

```python
doc = frappe.get_doc(doctype, name)
result = crema.transform(doctype, name, "Mark this order as urgent")
doc.update({**result["set"], **result["child_set"]})
doc.save()
```

## `extract` — propose records from a file

```python
def extract(doctype: str, file: str | bytes, instruction: str | None = None) -> dict
# returns {"records": [{"set": {...}, "child_set": {...}}, ...], "reason": str,
#          "confidence": float}
```

Read `file` with `ocr()`, and propose field values for one or more **new** `doctype`
documents. How many records the file holds — one, or several — is the model's own
call, not yours: a file describing a single thing always comes back as a one-element
`records` list. Before it spends any LLM call, this call checks create permission on
`doctype` — as the `extraction` interface's isolation user, inside the sandbox (see
[security.md](security.md#the-sandbox)). It never writes a document. `instruction`
reaches both the OCR pass and the field-mapping pass — use it for layout hints on a
recurring document layout. `confidence` comes from the OCR pass; check it before you
trust any record.

Apply each record the same way as `transform`:

```python
result = crema.extract("Purchase Invoice", file_url)
for record in result["records"]:
    doc = frappe.new_doc("Purchase Invoice")
    doc.update({**record["set"], **record["child_set"]})
    doc.insert()
```

## `transcribe` — speech to text

```python
def transcribe(file: str | bytes, *, language: str | None = None) -> dict
# returns {"text": str, "language": str | None, "duration": float | None}
```

Transcribe `file` — a File URL or raw audio bytes — with the `transcribe` interface's
configured model. `language` is an optional ISO-639-1 hint (`"en"`, `"fr"`, ...); the
provider still auto-detects when you omit it. This interface has no system prompt,
so the scan and the guard do not run — there is no user-supplied text to scan before
the provider call happens — and the trap does not run either, because there
is no system prompt to protect (see [security.md](security.md)). The system still
checks the budget and logs every call to Crema Log. The interface has no fallback: a
transcription call that cannot resolve raises `CremaConfigError` instead of silently
landing on a chat model.

## `health` / `is_configured` — a below-System-Manager status check

```python
def health(interface: str = "simple", *, live: bool = True) -> dict
# returns {"configured": bool, "ok": bool | None, "reachable": bool | None,
#          "provider": str | None, "model": str | None, "detail": str,
#          "spend": float, "budget": float}

def is_configured(interface: str = "simple") -> bool
```

`check_provider` (the Providers list's live Connection check) is
`only_for("System Manager")`, so nothing below that role can render its own health
badge from it — that's what these two are for. Neither is `@frappe.whitelist()`'d and
neither applies a role check of its own; call them in-process from behind whatever
gate your own surface needs (`frappe.only_for(("Fleet Manager", "System Manager"))`,
for example), the same way you'd call `ask`/`ocr`/`transcribe`. Never returns or logs
an `api_key`.

`health()` resolves `interface` with the same fallback walk `ask()` uses. If the
interface falls back, `provider`/`model`/`spend`/`budget` describe the interface
that actually resolved and would be billed, not the one you asked for. Unless
`live=False`, the call makes one live connectivity check against that interface's
provider. `configured=False` means the interface does not resolve to a usable
provider at all; `ok` and `reachable` are `None` in that case, because there is no
provider to check. `is_configured()` is the cheap, no-network shortcut:
`health(interface, live=False)["configured"]`.

To set an interface's model, provider, or monthly budget from code instead of the desk
— a migration, a provisioning script — see `crema.configure()` in
[configure.md](configure.md#procedure-e--configure-by-code).

## HTTP endpoints

Four call endpoints expose `ask`, `extract`, `ocr`, and `transform` over HTTP. All four
need the `System Manager` role or the `Crema User` role. A set of System-Manager
utility endpoints sits beside them — see the table further down.

### `POST /api/method/crema.api.ask_api`

| Parameter | Required | Notes |
|---|---|---|
| `interface` | yes | Not `advanced_ocr` — the system refuses it over HTTP. |
| `prompt` | yes | |
| `context` | no | |
| `response_json` | no | Ask for a JSON reply. Same effect as `ask_json`'s `response_format`; the reply still comes back as a string under `result` — parse it yourself. |

### `POST /api/method/crema.api.extract_api`

| Parameter | Required | Notes |
|---|---|---|
| `doctype` | yes | |
| `file_url` | yes | A File URL. Raw bytes are not accepted over HTTP. |
| `instruction` | no | |

### `POST /api/method/crema.api.ocr_api`

| Parameter | Required | Notes |
|---|---|---|
| `file_url` | yes | A File URL. Raw bytes are not accepted over HTTP. |

### `POST /api/method/crema.api.transform_api`

| Parameter | Required | Notes |
|---|---|---|
| `doctype` | yes | |
| `name` | yes | The document to read and propose a diff for. |
| `instruction` | yes | |

### `POST /api/method/crema.api.dry_run_automation`

System Manager only. Previews a Crema Automation Task — reads its source, plans, and
extracts — and stops before any write. Backs the **Dry Run** button; see
[automation.md](automation.md#dry-run).

| Parameter | Required | Notes |
|---|---|---|
| `task` | yes | The Crema Automation Task name. |

Returns `{action, used_stored_plan, plan, rows, row_count, note, allowed_names}`,
with `rows` capped at the first 20 — or `{action: "No Changes", report}`, or
`{action, row_count, note}` when the source yields nothing. Limited to 20 calls per
hour per client IP, and uses the same 417 error shape as the endpoints above.

### `POST /api/method/crema.api.trigger_automation`

Starts one **Crema Automation Task** from outside the site — the Webhook trigger. Unlike
every other endpoint here, this one is **not** System Manager only: the caller needs read
access to the task document, which an ordinary role grants.

Sign in with a Frappe API key and secret (`Authorization: token <key>:<secret>`).

| Field | Notes |
|---|---|
| `task` | Required. The task name. Refused unless its trigger is `Webhook` and it is on. |
| `payload` | Optional text, at most 20,000 characters. Read as one more source. |

Returns the id of the queued job. 60 calls per hour per client IP. See
[automation.md](automation.md).

### Utility endpoints

All System Manager only, all `POST /api/method/<name>`:

| Endpoint | Purpose |
|---|---|
| `crema.api.get_interfaces` | Value/label pairs for the AI Profile dropdown on Crema Automation Task, one per selectable interface. `Advanced OCR`, `View`, `Transform`, `OCR` and `Transcribe` are not included — a task must never run as any of them. The dropdown's option list itself comes from the field's own metadata; this endpoint only supplies the human-readable labels. |
| `crema.api.get_guardrails` | Value/label/help triples for every registered guardrail. Feeds the Guardrail picker and the check list on the Guardrails page. |
| `crema.api.get_models` | One provider's model list. Feeds the Model autocomplete in Crema Settings. |
| `crema.api.check_provider` | One live connection check for a provider. Feeds the Providers list's Connection badge. |
| `crema.api.grant_crema_role` | Adds the `Crema User` role to a user. System Manager only. |
| `crema.api.get_usage` | Month-to-date spend and budget per interface and per provider. Feeds the Usage column. |
| `crema.api.run_automation_now` | Enqueue one automation task run. Backs the Run Now button — see [automation.md](automation.md). |

### Rate limits

Two limits guard the four call endpoints. Each endpoint allows 60 calls per hour
per client IP; the buckets are separate per endpoint, so one IP can spend at most
240 calls per hour across the four. Each session user gets one shared bucket of 60
calls per hour across all four together. These limits do not apply to a Python
caller running in-process (bench console, a background job).

### Errors

A blocked prompt or document, a monthly budget already spent, or a configuration
error — for example, an unresolved interface — all return HTTP status 417, with the
same body shape:

```json
{"blocked": true, "reason": "..."}
```

`blocked` is `true` only for an actual security block; a budget or configuration stop
returns `blocked: false` with a `reason` explaining what happened. Read `blocked`, not
the status code, to tell a security block apart from a budget/configuration stop.

A Python caller sees these as three distinct exception types. `CremaBlockedError`:
the scan or the guard blocked the prompt. `CremaConfigError`: the interface does not
resolve to a usable provider, or the kill switch is enabled. `CremaBudgetError`: the
interface's, the provider's, or the user's monthly budget is already spent this
month — see [security.md](security.md#budgets). All three are
`frappe.ValidationError` subclasses, and over HTTP all three get the structured
`{"blocked": bool, "reason": ...}` body above.

## Desk UI

Every surface below needs the `System Manager` role or the `Crema User` role — see
[install.md](install.md) for how to grant it. Without either role, none of these
surfaces appear.

### List view

Open any doctype's list view. A robot button sits between the **Menu** button and
**Add**.

The button opens a dialog. Type what you want: a request can change the current list
view, create a record, or update or delete records it can find. It does only what your
permissions allow. If you may create a `doctype` document, the dialog also offers an
**Add a document** area below the request field — drop a file there and the same typed
text guides how Crema reads it. If you can't, the dialog offers the request field alone.

A typed request does exactly one of five things. Which one is the model's own call,
made from the same field schema either way — fields you cannot read never reach the
model, and fields you cannot write never reach a save.

- **Change the view.** The system turns the request into a filtered List, Report, or
  Kanban view of the current doctype and switches you to it, with an alert explaining
  why. A request can also set the sort order ("sorted by name descending"), limit the
  row count ("show me the top 10"), pick which columns show (Report view only), and
  group by a field with a count, sum, or average (Report view only). If you name no
  field, the system prefers one already shown as a column in the current list. A
  request that switches to Kanban does not carry filters with it — Kanban always
  opens on the board's own, unfiltered records; ask for a List or Report view for a
  filtered result. A request cannot match text case-sensitively — matching is always
  case-insensitive. An underscore in a request ("records starting with `_`") matches a
  literal underscore, not any character. The record ID is a field like any other, so a
  request can filter or find a record by it.
  And it cannot combine two different fields with OR: the system combines every filter
  with AND, so "starting with A or B" filters on neither field, and the alert says so
  instead of guessing.

  If the filtered view finds no records, the system does one more database query. It
  looks for the same text in every text field you can read, then applies the one
  field that holds it, and an alert tells you which field it used. This is a
  widening of one filter, not an OR: the result shows one field, not two. When a
  record's own value matches exactly, the system narrows to that exact match instead
  of a partial one. When nothing holds the whole phrase, the system tries each word in
  it on its own — "Acme Corp" finds a record named "Acme Corporation" this way, through
  the word "Acme". The system does not ask the model a second time, whichever of these
  it needed.
- **Create a record.** "Create a company called Acme" opens a new, unsaved form with
  its fields filled in — nothing is written until you save it yourself. A request
  naming several new records at once goes through the same **Import N Documents** step
  the file upload path uses below, and needs the `System Manager` role for the same
  reason Data Import always does.
- **Change existing records.** A request can name which records to change and what to
  change about them — "mark the Acme todo as closed". If it matches one record, the
  system shows the proposed change first, then opens that record's form with the
  change already filled in, unsaved, for you to review and save. If it matches more
  than one, the system asks you to confirm first: a dialog lists every record the
  request would touch, by name, before anything happens. A request naming no records
  to change is refused outright — it is never read as "change everything". A request
  that proposes changing only fields you cannot write is refused too, instead of
  saving the record unchanged.
- **Delete records.** Works the same way as changing records: a dialog lists every
  record the request would delete, by name, before anything is deleted. A request
  naming no records is refused outright.
- **None of the above.** A request the system cannot carry out here — exporting,
  emailing, a question with no view that answers it, a request about a different
  doctype, several requests at once — gets a plain message saying so, instead of a
  view that does not answer what you asked.
- **Drop a document onto the upload area.** The upload area takes one local file, by
  drag-and-drop or by clicking to pick it — there is no file browser, web link, or
  camera option. The system reads it (`extract_api`) and shows a preview — the proposed
  record(s), the model's reason, and the OCR confidence. How many records the document
  holds is the model's own call, not a checkbox you tick up front:
  - **Nothing found** — the document held no records the system could map to
    `doctype`. The system creates nothing.
  - **One record** — click **Create Document** to open a new, unsaved form with its
    fields filled in, including child table rows. The system writes nothing until
    you save the form yourself.
  - **Several records** — click **Import N Documents** to hand the records to a
    prefilled **Data Import**, which has its own preview step before it writes
    anything. This needs the `System Manager` role, same as Data Import always has.

### The search bar

Skip the dialog entirely: open a list view, then type a prompt into the search bar
at the top of Desk (Frappe's awesomebar). The option only appears while a list view is
open and your text does not start with `#` (the search bar's own prefix for jumping to
a doctype). An `Ask <your prompt>` option appears directly under the built-in
`Search for <your prompt>` entry — click it to run the same request the dialog's
**Go** button would, and switch the view immediately, with no dialog.

### Form view

Open any document you may edit. A robot button sits in the same icon group as the
reload button. It opens a dialog: type what should change, and the system proposes a
diff for the open document (`transform_api`). Click **Apply** to fill the proposed
values into the form — the form stays dirty and unsaved, exactly like the list
view's extract preview; save it yourself.

### When a prompt is blocked, or a request fails

A prompt the scan or the guard refuses — whether typed into a dialog, the search bar,
or a document dropped for extraction — comes back as a red **Blocked** message giving
the reason. The system sent nothing to the provider. The Crema Log still records the call,
with status `Blocked`, so the block rate stays visible. See
[security.md](security.md) for what the scan and the guard check.

A monthly budget already spent, or an interface that can't be resolved to a usable
provider, likewise comes back as a red message naming the reason, titled **Crema**
rather than **Blocked**. Any other failure (a network error, an expired session, a
server error) shows a message too — nothing in the desk UI fails without telling you.

### Usage and the workspace

Open **Crema** in the Desk app list — the icon is a robot. The sidebar holds
**Crema Settings**, **Home**, a **Configuration** section (Crema Provider, Crema
Automation Task), and a **Monitoring** section (Crema Log, Crema Usage).

The workspace home shows three number cards, all-time totals: **Crema Calls**, **Crema
Cost (USD)**, and **Crema Blocked**.

The **Crema Log** list shows the interface name as the row title, with the user, the
status, and the cost beside it. Use the **Interface** and **Status** filters at the top
of the list to narrow it. On one row, a **Diagnostics** section holds the model, the
provider, the prompt hash, the duration, and the per-call token counts. It opens
automatically for a `Blocked` row, an `Error` row, or a row with more than one provider
call.

The **Crema Usage** report filters by From Date, To Date, Interface, and Status
(`Success`/`Cached`/`Blocked`/`Error`), and groups by Interface, Model, User, or Day.
Columns: Calls, Cached, Blocked, Errors, Prompt Tokens, Completion Tokens, Cost (USD),
and Avg Duration (ms), with a total row and a bar chart. See
[security.md#budgets](security.md#budgets) for the matching Usage column in Crema
Settings.

## Caching a response

Set `cache_ttl` on the interface to cache every `ask`/`ask_json` call to it for that
many seconds. Set the `cache_ttl` argument on one call to override the interface
setting for that call only. A `cache_ttl` of 0 turns caching off. The cache covers
`ask`/`ask_json` (and everything built on them) only — `ocr`, `advanced_ocr`, and
`transcribe` never read it.

A call that gives `files` is never cached, whatever `cache_ttl` says. The cache key is
made from the prompt, and it cannot see the content of a file. A file behind a File URL
can also change while the URL stays the same.

## Testing an app that uses crema

An app that hard-depends on crema needs `client._resolve()` to succeed before its own
tests reach the mock — otherwise every call in its suite raises `CremaConfigError`
first. `crema.testing.seed_provider()` is the supported way to get there, meant for a
consuming app's own `before_tests`:

```python
# my_app/setup.py
from crema.testing import seed_provider

def before_tests():
    seed_provider()
    frappe.db.commit()  # nosemgrep: frappe-manual-commit — fixture must outlive this transaction
```

Safe to re-run and non-overriding: it fills in a `Crema Provider` and a `Crema Settings`
default only where one doesn't already exist, so calling it twice, or on a site an
admin already configured by hand, changes nothing. See its docstring for the
`base_url`/`model`/`isolation_user` arguments.

With the site configured this way, `client._complete(cfg, messages,
response_format=None)` is the supported patch point for a caller's test — the single
place a provider is actually called, so patching it exercises everything above it
(the guardrails, cache, budget, sandbox, audit log). Its signature is covered by the
same stability expectation as the functions `crema/__init__.py` exports:

```python
from unittest.mock import patch

with patch("crema.client._complete", return_value="mocked reply"):
    crema.ask("simple", "hello")
```
