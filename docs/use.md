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
the provider call happens — and the output trap does not run either, because there
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

`check_provider` (the Crema Settings Providers panel's live check) is
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

## HTTP endpoints

Three call endpoints expose `ask`, `extract`, and `transform` over HTTP. All three
need the `System Manager` role or the `Crema User` role. A set of System-Manager
utility endpoints sits beside them — see the table further down.

### `POST /api/method/crema.api.ask_api`

| Parameter | Required | Notes |
|---|---|---|
| `interface` | yes | Not `security` or `advanced_ocr` — the system refuses these over HTTP. |
| `prompt` | yes | |
| `context` | no | |
| `response_json` | no | Ask for a JSON reply. Same effect as `ask_json`'s `response_format`; the reply still comes back as a string under `result` — parse it yourself. |

### `POST /api/method/crema.api.extract_api`

| Parameter | Required | Notes |
|---|---|---|
| `doctype` | yes | |
| `file_url` | yes | A File URL. Raw bytes are not accepted over HTTP. |
| `instruction` | no | |

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
| `crema.api.get_interfaces` | Value/label pairs for the AI Profile dropdown on Crema Automation Task, one per selectable interface. `Security`, `Advanced OCR`, `View` and `Transform` are not included — a task must never run as any of them. The dropdown's option list itself comes from the field's own metadata; this endpoint only supplies the human-readable labels. |
| `crema.api.get_models` | One provider's model list. Feeds the Model autocomplete in Crema Settings. |
| `crema.api.check_provider` | One live connection check for a provider. Feeds the Providers panel's Connection badge. |
| `crema.api.get_usage` | Month-to-date spend and budget per interface and per provider. Feeds the Usage column. |
| `crema.api.run_automation_now` | Enqueue one automation task run. Backs the Run Now button — see [automation.md](automation.md). |

### Rate limits

Two limits guard the three call endpoints. Each endpoint allows 60 calls per hour
per client IP; the buckets are separate per endpoint, so one IP can spend at most
180 calls per hour across the three. Each session user gets one shared bucket of 60
calls per hour across all three together. These limits do not apply to a Python
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
resolve to a usable provider. `CremaBudgetError`: the interface's monthly budget is
already spent this month — see [security.md](security.md#budgets). All three are
`frappe.ValidationError` subclasses, and over HTTP all three get the structured
`{"blocked": bool, "reason": ...}` body above.

## Desk UI

### List view

Open any doctype's list view. A robot button sits between the **Menu** button and
**Add**. It needs the `System Manager` role or the `Crema User` role — see
[install.md](install.md).

The button opens a dialog. Type what you want first — a request changes the current
list view; if you also drop a document below, the same text guides how that document
is read. The upload area only appears if you may create a `doctype` document; if you
can't, the dialog offers the request field alone.

- **Type a request.** The system turns it into a filtered List, Report, or Kanban view
  of the current doctype (`ask_api` with the `view` interface) and switches you to it,
  with an alert explaining why. The schema sent to the model, and any field name it
  proposes back, are both filtered down to fields you have at least read access to. A
  request can also set the sort order ("sorted by name descending"), limit the row
  count ("show me the top 10"), and group by a field — all applied to the same view.
  If you name no field, the system prefers one already shown as a column in the
  current list. A request cannot do two things. It cannot match text
  case-sensitively — matching is always case-insensitive. And it cannot combine two
  different fields with OR: the system combines every filter with AND, so "starting
  with A or B" filters on neither field, and the alert says so instead of guessing.

  If the filtered view finds no records, the system does one more database query. It
  looks for the same words in every text field you can read, then applies the one
  field that holds them, and an alert tells you which field it used. This is a
  widening of one filter, not an OR: the result shows one field, not two. The system
  does not ask the model a second time.
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
at the top of Desk (Frappe's awesomebar). This needs the same role as the robot
button. The option only appears while a list view is open and your text does not
start with `#` (the search bar's own prefix for jumping to a doctype). An
`Ask <your prompt>` option appears directly under the built-in
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

Open **Crema** in the Desk app list — the icon is a robot. The sidebar holds **Crema
Settings**, **Home**, a **Configuration** section (Crema Provider, Crema Automation
Task), and a **Monitoring** section (Crema Log, Crema Usage).

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
