# Configure Crema

Configure two things before you call Crema: a provider and an interface. A provider
holds a connection to an LLM API. An interface binds a provider and a model to one
named use-case. The safety checks live on the **Guardrails** page, one ordered list
for the whole site (Procedure D).

Add the provider first. **Providers** is a list of its own, first in the Crema
workspace sidebar. Interfaces live on **Crema Settings**, below it in the same
sidebar: the defaults and a **Use Cases** grid. Crema Settings tells you when no
provider is switched on, because nothing on that page can work until one is.

The Crema workspace is part of the app. If you change its layout, the system saves
your change apart from it, not into it.

## Procedure A — add a provider

Caution: the system refuses to enable a provider with no API key, unless the base URL
is a local or private address (for example, a local Ollama server).

1. Open the **Providers** list and click **New from Template**.
2. Pick a preset — OpenAI, OpenRouter, Groq, or one of about twenty more, hosted and
   local. The system fills in the provider name and base URL. The provider name must be
   unique — change it when you add a second key for the same vendor.
3. Paste your API key into the **API Key** field.
4. Leave **Enabled** ticked, and leave **Set as default provider** ticked unless you
   already have a default and are only adding a second provider.
5. Click **Create**.

Every enabled provider in the list shows a live **Connection** status next to
Enabled/Disabled — green with a model count on success, red with the error otherwise
(unauthorized, timeout, unreachable host). The page checks it fresh on every load,
with no cache, so a revoked key shows red immediately.

Deleting a provider that Crema Settings names — as the Default Provider, or on a
use case's own row — clears that reference instead of blocking the delete. The
provider's form warns you first, and a message names what it cleared once the
delete is done.

### Crema Provider fields

| Field | Type | Notes |
|---|---|---|
| `provider_name` | Data | Label **Name**. Required. Unique. |
| `base_url` | Data | Label **Address**. Required. The OpenAI-compatible API base, for example `https://api.openai.com/v1`. |
| `api_key` | Password | Required to enable, unless the base URL is local or private. |
| `enabled` | Check | Off by default. |
| `timeout_seconds` | Int | Label **Wait Up To (seconds)**. 60 by default. Raise this for a local or self-hosted provider, for example Ollama on CPU. |
| `monthly_budget_usd` | Currency | 0 (default) means unlimited. A combined ceiling across every interface using this provider — see [security.md](security.md#budgets). |

## Procedure B — set the defaults (the fast path)

Every interface works as soon as you set three fields, above the Use Cases
grid:

1. **Default Provider** — the provider from Procedure A. Already filled in if you left
   "Set as default provider" ticked there.
2. **Default Model** — a model from that provider. This field stays locked until you
   set a Default Provider, then lists that provider's models. The system warns on
   save when the value is blank or no longer matches the provider's model list, but
   still lets you save. Fill it in before you call an interface that needs it.
3. **Default Runs-As User** — pre-filled on install with a dedicated, unprivileged
   account (`crema@<site>`, no password, `Crema User` role only) created for exactly
   this. Keep it unless you want a different account as the default isolation user.

Save. Every one of the 11 predefined interfaces now resolves through these three
values.

Note: on this fast path, `advanced_ocr` resolves to the same provider and model as
`ocr`, so OCR escalation never fires — a retry on the same model has no value, and
the system skips it. To make escalation real, give the `advanced_ocr` row its own,
stronger provider or model in Procedure C.

## Procedure C — override one interface

The **Use Cases** grid always lists every interface, one row each, by its
human-readable name — `Default`, `Translation`, `OCR`, and so on. See the reference
table below for the full list and each row's underlying key. There is nothing to
create, and you cannot add or remove a row: the grid has no add/delete controls, and
the system reconciles it to that exact list every time you save Crema Settings,
including on install and migrate.

The grid stays collapsed as long as every row is pure default (nothing set in
Provider, Model, or Runs As), and opens automatically once any row has an
override. A blank cell in Provider, Model, or Runs As always means "use the
matching Default from Procedure B" — fill in a cell only to point that one interface
somewhere else.

Only **Provider** and **Model** are editable directly in the grid row. Everything
else — Runs As, the monthly budget, Creativity, how long answers are reused, the
standing instructions — lives behind the pencil (**Edit**) icon at the end of the
row, which opens that use case's full field set. The safety checks live on the
**Guardrails** page, one ordered list for the whole site with a per-check use-case
filter (Procedure D).

Warning: pick a low-privilege isolation user, for a row override same as for the
default. The system refuses `Administrator` and any user with the `System Manager`
role, because `System Manager` can read the provider's API key. Either choice would
remove the sandbox.

1. In the row for the interface you want to override, pick a **Provider** different
   from the default directly in the grid.
2. Pick a **Model**. This column stays empty until this row has its own provider; the
   model list comes live from that provider.
3. Click the row's **Edit** icon to set **Runs As**, if this interface needs a different
   account from the default. One automation task can also set its own **Runs As**, which
   wins over this one — use that when two tasks share a use case but must read as
   different accounts. A row that sets a Provider without its own Runs As user
   still uses the Default Runs-As User.
4. Click **Save** (the normal form save — this is one document, Crema Settings).

**Reset All Use Cases**, in the grid's toolbar, blanks every row's Provider, Model,
Runs As, monthly budget, Creativity, answer reuse, and standing instructions in one
action — every interface falls back to the Defaults above. It does not touch
`max_tokens`, and it does not touch the Guardrails page. The action only changes the
fields on screen; the form stays unsaved, so review before you click Save, or reload
the page to discard.

The **Usage** column shows this month's spend against each interface's effective
monthly budget (its own, or the Default Monthly Budget). An unbudgeted interface
shows a grey dollar amount. A budgeted one shows a percentage: green under 80%,
orange 80–99%, red at 100% or over. See [security.md](security.md#budgets).

### Crema Model Assignment fields (one row in the grid)

| Field | Type | Notes |
|---|---|---|
| `interface_label` | Data | Label **Use Case**. Read-only. The human-readable name shown in the grid's first column (see the reference table below). |
| `interface` | Data | Label **Use Case Key**. Read-only. The key passed to `ask()` and friends — the system reconciles the full set on every save; you never create one by hand. |
| `provider` | Link | Blank uses Crema Settings' Default Provider; if that's blank too, the interface falls back per the chain below. |
| `model` | Autocomplete | Blank uses the Default Model. Fetched from this row's own provider once one is set, or from the Default Provider if this row leaves Provider blank too. |
| `usage` | Data | Read-only, never saved. Painted live from this month's Crema Log spend each time the page loads. |
| `isolation_user` | Link (User) | Label **Runs As**. Not `Administrator`. Not a `System Manager`. Not a disabled user. Blank uses the Default Runs-As User. An effective provider (this row's or the default's) always needs an effective Runs As user (this row's or the default's). A single automation task can override this — see [automation.md](automation.md). |
| `monthly_budget_usd` | Currency | 0 (default) means "use the Default Monthly Budget"; 0 there too means unlimited. See [security.md](security.md#budgets). |
| `temperature` | Float | Label **Creativity**. 0 by default. |
| `max_tokens` | Int | Label **Longest Answer**. Caps the response length. 0 (default) leaves the provider's own default untouched. |
| `cache_ttl` | Int | Label **Reuse Answers For (seconds)**. Seconds to cache a response. 0 turns caching off. Applies to `ask`/`ask_json` calls (and everything built on them) only — `ocr`, `advanced_ocr`, and `transcribe` never read the cache. |
| `system_prompt` | Long Text | Label **Standing Instructions**. Fills in from a built-in default if you leave it empty. |

Set `cache_ttl` on the `view` row to make a repeated desk "Ask Crema" list-view request
free: Crema serves the exact same request on the same doctype from cache, with no
provider call, until the TTL expires.

## Procedure D — set the guardrails

Open **Guardrails** (under Setup, next to Settings). One list holds every safety
check, in the order they run. The page lists each available check above the grid,
with one line on what it does. What each check guarantees, and which positions in
the list are fixed, is on [security.md](security.md#guardrails).

Note: the shipped rows are created once. A row you delete stays deleted — a
later migrate does not bring it back. Add a row and pick the same check to restore
it.

1. Drag a row to change the order the checks run in.
2. Set each row's **Action**: `Off`, `Log Only` (record on the Crema Log row,
   proceed), `Retry Once` (the Reply Check only; the other checks treat it as
   `Block`), or `Block`.
3. Leave **Use Cases** empty to run the row for every use case, or enter a
   comma-separated list of use-case keys to limit it.
4. Save.

Crema ships these rows, in this order:

| Row | Default | Notes |
|---|---|---|
| **Text Scan** | `Block` | The local, no-cost scan for prompt injection. |
| **Hide Personal Information** | `Off` | Experimental. Masks names, emails, phone numbers, and record values before the request leaves the server, and swaps them back into the reply. |
| **Hide Health Information** | `Off` | Experimental. Masks a list of condition, medication, and procedure words. A keyword list, not a detector — read [security.md](security.md#masking-experimental) before you rely on it. |
| **Reply Check** | `Block` | Puts a token in each request and checks that the reply returns it. |

You can add a second row for the same check — for example a second Hide row
filtered to a different use case — each keeps its own state. A row whose check came
from an app you removed disappears on the next save.

Audio: none of these checks can read sound. For the Transcribe use case, only a Hide
row set to `Block` changes anything — it refuses the call. The page warns when a row
names `transcribe` in its filter.

To get an alert when something is blocked, enable the shipped Notification "Crema
blocked a call" (disabled by default) and set its recipients.

### Health Words

Crema's built-in health word list (used by **Hide Health Information**) is English
only. To add words in another language, open **Health Words** and either type a word
in yourself, or click **Translate Built-in List**, pick a language, and Crema asks its
Translation use case to propose that language's version of the list. Proposed words
are filed switched off — review them and turn on the ones you want before they take
effect. A word added by hand starts switched on.

| Field | Type | Notes |
|---|---|---|
| `word` | Data | The word or phrase to hide. Must be unique. |
| `language` | Link (Language) | Set automatically by Translate Built-in List; not read when matching. |
| `enabled` | Check | Off by default for a translated word; on by default for a word you add yourself. |

### Crema Guardrail fields (one row in the list)

| Field | Type | Notes |
|---|---|---|
| `guardrail` | Select | Label **Guardrail**. The check this row runs. The options list every built-in and every app-registered check. |
| `action` | Select | Label **Action**. `Off` (default), `Log Only`, `Retry Once`, or `Block`. |
| `interfaces` | Data | Label **Use Cases**. Empty means every use case; a comma-separated list of use-case keys limits the row. |

## Procedure E — configure by code

For a migration or a provisioning script, not the desk: `crema.configure()` sets
**Provider**, **Model**, or the monthly budget on one interface's row without opening
the form.

```python
import crema

crema.configure("transcribe", model="whisper-1")
```

Every argument left out is left untouched on the row — this is not a replace. A row
that doesn't exist yet (a brand-new interface name never saved before) is created
first, the same way saving Crema Settings in the desk always would. An unknown
interface name raises `CremaConfigError`. Calling it again with the same arguments
changes nothing.

It returns the resolved config afterward — `health(interface, live=False)` — which
reports `{"configured": False, ...}` rather than raising if the interface still
doesn't resolve to a usable provider (for example, a model set before any provider
exists yet). Setting `provider` with no isolation user anywhere still raises, same as
saving the form with that row would — `configure()` does not set one.

Only these three fields. Everything else on a row — the standing instructions, Runs
As, the Use Cases grid's own budget field — stays a desk edit.

### Crema Settings fields (the rest of the page)

| Field | Type | Notes |
|---|---|---|
| `default_provider` | Link | Used by any row that leaves Provider blank. |
| `default_model` | Autocomplete | Used by any row that leaves Model blank. Locked until Default Provider is set; lists that provider's models. |
| `default_isolation_user` | Link (User) | Label **Default Runs-As User**. Used by any row that leaves Runs As blank. Seeded on install/migrate — see [install.md](install.md). |
| `default_monthly_budget_usd` | Currency | Used by any row that leaves its own monthly budget at 0. 0 here too means unlimited. |
| `default_monthly_budget_usd_per_user` | Currency | Label **Per-User Monthly Budget (USD)**. The most one user may spend per calendar month across all interfaces. Skipped for the Administrator and for automation runs. 0 means no per-user limit. |
| `log_retention_days` | Int | Label **Keep Logs For (days)**. 30 by default. How long a Crema Log row survives before the daily cleanup job deletes it. |
| `blocked_doctypes` | Table | Label **Blocked Doctypes**. Empty by default. Record types the AI may never create, edit, or delete — see "Block a doctype outright" below. |
| `disabled` | Check | Label **Crema Is Off**. Switch this on to stop every LLM call. A kill in `site_config.json` (key `crema_disabled`) has the same effect and a desk edit cannot undo it. Either one kills; clearing both restores service. |

## Block a doctype outright

The Blocked Doctypes table under **Restrictions** is the site's own red line: a record
type listed there is refused by every automation task and by the desk assistant's
write actions, whatever an interface's own Runs As account could otherwise reach. Add a
row, pick the doctype, and save — no migrate, no restart. The rule applies immediately
to automation (the next run checks it) and to the desk (the next page load picks it up).

This does not touch reading. A blocked doctype still appears in list views, in a
Document Query source, and in what the desk assistant's "view" action can filter and
show — only create, edit, and delete are refused. See
[docs/security.md](security.md#a-site-wide-write-block) for the full guarantee and its
limits.

## Procedure F — give people access

A provider and an interface are configured now, but the desk robot UI stays invisible
to everyone except a System Manager until you grant the `Crema User` role. See
[install.md](install.md) for what the role gates and how it fails.

1. Open the **User** record for the person who needs access, go to the **Roles** tab,
   and check **Crema User**.
2. Ask them to reload Desk. The robot button appears on list views and forms, and
   `Ask …` appears in the search bar.

## Reference — interfaces and the fallback chain

| Interface | Use Case | Purpose | Fallback |
|---|---|---|---|
| `simple` | Default | General-purpose, lightweight calls | none — end of the chain |
| `translation` | Translation | Faithful translation, preserving tone and meaning | `simple` |
| `complex` | Complex | Careful, thorough reasoning for demanding tasks | `simple` |
| `ocr` | OCR | Extract text from images or scanned PDFs | none |
| `advanced_ocr` | Advanced OCR | Stronger retry when `ocr` reports low confidence | none — escalation target only |
| `extraction` | Extraction | Pull structured data out of content | `complex` |
| `classification` | Classification | Classify or label content | `simple` |
| `summarization` | Summarization | Concise, accurate summaries | `simple` |
| `transform` | Transform | Propose a diff for an ERP document | `complex` |
| `view` | List Assistant | Turn a prompt into a view, a new record, or an edit/delete for the desk UI | `complex` |
| `transcribe` | Transcribe | Speech-to-text via `crema.transcribe()` | none — a transcription call cannot fall back to a chat model |

The Crema Automation Task **AI Profile** dropdown offers six of these. It leaves out
`advanced_ocr`, which a task must never run as; `view` and `transform`, whose answers
only the caller that asked for them can apply; `ocr`, whose answer is a page of text
and not a plan; and `transcribe`, which speaks to a speech provider and not to a chat
provider.

If an interface has no provider of its own, the system tries Crema Settings' Default
Provider first. Only if that is also blank does it try the next interface in its
fallback chain. If nothing resolves this way (including `simple`, the end of every
chain), the system raises `CremaConfigError`.

A fallback supplies the provider connection, the model, the isolation user, and the
billing identity — the Crema Log row and the monthly budget follow the interface
that actually served the call. The system keeps two things from the interface you
asked for. First, its prompt: `view`, `transform`, and `extraction` each expect
strict JSON back, and the fallback's own prompt would produce plain text the caller
cannot parse. Second, its guardrails: every Guardrails row is matched against the
use case you asked for, so a fallback never lowers the requested interface's
protection.

An installed app can add its own interfaces too — see below. Those rows appear in the
grid alongside the 11 above, same reconcile, same locked add/delete.

## How to add a new interface name

**From another installed app (no crema edit):** add a `crema_interfaces` dict to that
app's `hooks.py` — either inline:

```python
crema_interfaces = {
    "my_app_ocr": {"prompt": "...", "fallback": "ocr"},
}
```

or, if the prompt text lives in its own module, a dotted path string to the dict
instead (resolved lazily via `frappe.get_attr` — the same convention Frappe's own
`after_install`/scheduler hooks use, so that module is only imported the first time a
caller actually asks something, not on every process boot):

```python
crema_interfaces = "my_app.llm.interfaces.INTERFACES"
```

Always set `prompt` and `fallback`. The system does not enforce them: a missing
`prompt` gives the interface an empty prompt, and a missing `fallback` gives it no
fallback chain. Set `label` too, or the Use Cases grid and the Crema Log show the raw
key (`my_app_ocr`) instead of a human-readable name — `label` is read the same way as
`prompt`/`fallback`, not written onto the row. Every other key is a `Crema Model
Assignment` fieldname (`isolation_user`, `monthly_budget_usd`, `max_tokens`, ...) that
the system seeds onto the row the first time it creates it — an admin can change any
of it afterward in the desk. The system ignores a name already in the 11 predefined
interfaces, so an app can never redefine `ocr` or `simple`. Run `bench migrate` (or
restart) so the new row appears.

**In crema itself**, for a new predefined interface that talks to a provider the same
way `ask()` already does (a chat completion):

1. Open `crema/interfaces.py`.
2. Add the name to the `PREDEFINED` list.
3. Add a default system prompt to `DEFAULT_PROMPTS`.
4. If the interface should degrade to another one, add it to `FALLBACKS`.

That shape needs no other code change. An interface that calls a different provider
endpoint needs more. The precedent is `transcribe`, which calls
`litellm.transcription`, not `litellm.completion`. Such an interface needs its own
function next to `client._complete`, and its own entry point in `crema/api.py` (see
`transcribe()`), because it has no system prompt to run through `_resolve`'s usual
chat-message path. It also skips `DEFAULT_PROMPTS`, and usually `FALLBACKS`: a
transcription call cannot fall back to a chat model, and an OCR call cannot fall
back to a transcription one. Each non-chat interface's fallback chain, if it has
one, stays within its own kind.

## Add your own guardrail

An installed app can add a check to the Guardrails list — an external toxicity API, a
stronger PII detector — with no crema edit. Add a `crema_guardrails` dict to that
app's `hooks.py`:

```python
crema_guardrails = {
    "toxicity": "my_app.guardrails.TOXICITY",
}
```

Each value is a module object, or a dotted path string to one (resolved lazily, the
same convention as `crema_interfaces`). A module is any object with:

- `key`, `label`, and a `default_action` (usually `"Off"`). Give it a one-line
  `help` string too — the Guardrails page shows it in the check list above the grid.
- an optional `before(ctx)` — read `ctx.messages`, raise `crema.CremaBlockedError`
  to block, or record a note per `ctx.action`,
- an optional `after(ctx)` — read and, if needed, replace `ctx.response`.

Set `pre_cache = True` on the module to run it before the answer cache and the
budget check instead, in the position the Text Scan holds, over `ctx.text` (the
joined request text) rather than `ctx.messages`. A pre-cache check runs on every
call, cached or not, so it must be cheap and must not call a provider.

`ctx.action` is the row's Action and `ctx.interface` the requested use case.
`ctx.state` is a scratch dict that survives a retry: a Reply Check row set to `Retry
Once` re-runs every `before()` hook against the caller's original messages, with
`ctx.state` carried over. Reach for `ctx.slot(ctx.row)` instead of `ctx.state`
directly if your check should support more than one row on the same site — crema's
own masking rows do this, so two Hide rows never share a vault.

If your check masks or otherwise hides content from the request, set
`masks_text = True` on the module too. Audio and image attachments carry no text to
hide anything in; this flag is what makes a row set to `Block` refuse such a call.

After `bench migrate` the new key appears in the Guardrail picker; the admin adds a
row and picks it, in whatever position they want. An app can never replace one of
crema's own checks, and the first installed app wins a key collision.

Next step: [use.md](use.md).
