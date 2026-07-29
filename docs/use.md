# Use Crema

This page covers the calls a developer makes: from Python, or over HTTP.

## `ask` — call an interface

```python
def ask(interface_or_prompt: str, prompt: str | None = None, *, context: str | None = None,
        files: list[str] | None = None, response_format: dict | None = None,
        cache_ttl: int | None = None) -> str
```

Send `prompt` to the named `interface`. The call returns the model's text response.

- `context` adds background text. The system scans `context` and `prompt` together,
  because an attack can split across the two fields.
- `files` is a list of File URLs. The system sends readable files as vision input,
  after a permission check on each file.
- `response_format` requests a structured reply, for example
  `{"type": "json_object"}`.
- `cache_ttl` overrides the interface's cache setting for this one call.

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

Same as `ask`, and also parses the reply as JSON. Use this when the interface's
prompt asks for a JSON object.

## `ocr` — read text from a file

```python
def ocr(file: str | bytes, instruction: str | None = None) -> dict
# returns {"text": str, "confidence": float, "escalated": bool}
```

Read `file` — a File URL or raw bytes. `file` may be an image, a scanned PDF, or a
text PDF. The system picks the right method for each case. If the `ocr` interface
reports low confidence, the system retries once with `advanced_ocr`; `escalated`
tells you if this happened. Use `instruction` to state layout hints, for example
"the invoice number sits in the top-right box".

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

## `extract` — smart import from a file

```python
def extract(doctype: str, file: str | bytes, instruction: str | None = None) -> dict
# returns {"records": [{"set": {...}, "child_set": {...}}, ...], "reason": str,
#          "confidence": float}
```

Read `file` with `ocr()`, and propose field values for one or more **new** `doctype`
documents. How many records the file holds — one, or several — is the model's own
call, not yours: a file describing a single thing always comes back as a one-element
`records` list. This call checks that you may create a `doctype` document before it
spends any LLM call. It never writes a document. `instruction` reaches both the OCR
pass and the field-mapping pass — use it for layout hints on a recurring document
layout. `confidence` comes from the OCR pass; check it before you trust any record.

Apply each record the same way as `transform`:

```python
result = crema.extract("Purchase Invoice", file_url)
for record in result["records"]:
    doc = frappe.new_doc("Purchase Invoice")
    doc.update({**record["set"], **record["child_set"]})
    doc.insert()
```

## HTTP endpoints

Three endpoints expose `ask`, `extract`, and `transform` over HTTP. All three need
the `System Manager` role or the `Crema User` role.

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

### Rate limits

Two limits apply to all three endpoints together: 60 calls per hour per client IP,
and 60 calls per hour per session user. A user's budget is shared across all three.
These limits do not apply to a Python caller running in-process (bench console, a
background job).

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

A Python caller sees these as three distinct exception types: `CremaBlockedError`
(the security scan or guard blocked the prompt), `CremaConfigError` (the interface
can't be resolved to a usable provider), and `CremaBudgetError` (the interface's
`monthly_budget_usd` is already spent this month — see
[security.md](security.md#budgets)). All three are `frappe.ValidationError`
subclasses and all three get the structured `{"blocked": bool, "reason": ...}` body
above over HTTP.

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
  current list. Two things a request cannot do: match text case-sensitively (matching
  is always case-insensitive), and combine two different fields with OR — every
  filter you ask for is combined with AND, so "starting with A or B" filters on
  neither, and the alert says so instead of guessing.

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
    `doctype`. Nothing is created.
  - **One record** — click **Create Document** to open a new, unsaved form with its
    fields filled in, including child table rows. Nothing is written until you save it
    yourself.
  - **Several records** — click **Import N Documents** to hand the records to a
    prefilled **Data Import**, which has its own preview step before it writes
    anything. This needs the `System Manager` role, same as Data Import always has.

### The search bar

You can also skip the dialog entirely: with a list view open, type a prompt into the
awesomebar (top of Desk). This needs the same role as the robot button, and only
appears while a list view is open and your text doesn't start with `#` (the
awesomebar's own prefix for jumping to a doctype). An `Ask <your prompt>` option
appears directly under the built-in `Search for <your prompt>` entry — click it to run
the same request the dialog's **Go** button would, and switch the view immediately,
with no dialog.

### Form view

Open any document you may edit. A robot button sits in the same icon group as the
reload button. It opens a dialog: type what should change, and the system proposes a
diff for the open document (`transform_api`). Click **Apply** to fill the proposed
values into the form — the form is left dirty and unsaved, exactly like the list
view's extract preview; save it yourself.

### When a prompt is blocked, or a request fails

A prompt the scan or the guard refuses — whether typed into a dialog, the search bar,
or a document dropped for extraction — comes back as a red **Blocked** message giving
the reason. Nothing was sent to the provider. The Crema Log still records the call,
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

Set `cache_ttl` on the interface to cache every call to it for that many seconds. Set
the `cache_ttl` argument on one call to override the interface setting for that call
only. A `cache_ttl` of 0 turns caching off.
