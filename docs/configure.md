# Configure Crema

Configure two things before you call Crema: a provider and an interface. A provider
holds a connection to an LLM API. An interface binds a provider, a model, and a
security setting to one named use-case.

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

Save. Every one of the 12 predefined interfaces now resolves through these three
values, including `security` — turning on `enable_llm_guard` anywhere no longer needs
`security` configured with its own provider first.

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
safety checks, the standing instructions — lives behind the pencil (**Edit**) icon at
the end of the row, which opens that use case's full field set.

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
action — every interface falls back to the Defaults above. It also sets every row's
security toggles back to their defaults: `enable_prompt_scan` on, `enable_llm_guard`
off. It does not touch `output_trap` or `max_tokens`. The action only changes the
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
| `enable_prompt_scan` | Check | Label **Text Scan**. On by default. Layer 1 — the local scan. |
| `enable_llm_guard` | Check | Label **AI Guard**. Off by default. Layer 2 — the LLM guard. Needs an effective `security` provider (its own row, or Default Provider). |
| `output_trap` | Select | Label **Reply Check**. `Block` by default (a freshly-reconciled row; an existing row from before this field was added stays at `Off` until you pick a value — see [security.md](security.md#layer-3--the-output-trap)). Layer 3 — the output trap. `Off` / `Log Only` / `Retry Once` / `Block`. |
| `system_prompt` | Long Text | Label **Standing Instructions**. Fills in from a built-in default if you leave it empty. |

Set `cache_ttl` on the `view` row to make a repeated desk "Ask Crema" list-view request
free: Crema serves the exact same request on the same doctype from cache, with no
provider call, until the TTL expires.

## Procedure D — configure by code

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

Only these three fields. Everything else on a row — the security toggles, the
standing instructions, Runs As — stays a desk edit.

### Crema Settings fields (the rest of the page)

| Field | Type | Notes |
|---|---|---|
| `default_provider` | Link | Used by any row that leaves Provider blank. |
| `default_model` | Autocomplete | Used by any row that leaves Model blank. Locked until Default Provider is set; lists that provider's models. |
| `default_isolation_user` | Link (User) | Label **Default Runs-As User**. Used by any row that leaves Runs As blank. Seeded on install/migrate — see [install.md](install.md). |
| `default_monthly_budget_usd` | Currency | Used by any row that leaves its own monthly budget at 0. 0 here too means unlimited. |
| `log_retention_days` | Int | Label **Keep Logs For (days)**. 30 by default. How long a Crema Log row survives before the daily cleanup job deletes it. |
| `blocked_doctypes` | Table | Label **Blocked Doctypes**. Empty by default. Record types the AI may never create, edit, or delete — see "Block a doctype outright" below. |

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

## Procedure E — give people access

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
| `security` | Security | Layer 2 guard: classifies a prompt's risk | none — raises an error if unresolved |
| `extraction` | Extraction | Pull structured data out of content | `complex` |
| `classification` | Classification | Classify or label content | `simple` |
| `summarization` | Summarization | Concise, accurate summaries | `simple` |
| `transform` | Transform | Propose a diff for an ERP document | `complex` |
| `view` | List Assistant | Turn a prompt into a view, a new record, or an edit/delete for the desk UI | `complex` |
| `transcribe` | Transcribe | Speech-to-text via `crema.transcribe()` | none — a transcription call cannot fall back to a chat model |

The Crema Automation Task **AI Profile** dropdown offers six of these. It leaves out
`security` and `advanced_ocr`, which a task must never run as; `view` and `transform`,
whose answers only the caller that asked for them can apply; `ocr`, whose answer is a
page of text and not a plan; and `transcribe`, which speaks to a speech provider and not
to a chat provider.

If an interface has no provider of its own, the system tries Crema Settings' Default
Provider first. Only if that is also blank does it try the next interface in its
fallback chain. If nothing resolves this way (including `simple`, the end of every
chain), the system raises `CremaConfigError`.

A fallback supplies the provider connection, the model, the isolation user, and the
billing identity — the Crema Log row and the monthly budget follow the interface
that actually served the call. The system keeps two things from the interface you
asked for. First, its prompt: `view`, `transform`, and `extraction` each expect
strict JSON back, and the fallback's own prompt would produce plain text the caller
cannot parse. Second, its security settings — `enable_prompt_scan`,
`enable_llm_guard`, and `output_trap` — so a fallback row with weaker settings never
lowers the requested interface's protection.

An installed app can add its own interfaces too — see below. Those rows appear in the
grid alongside the 12 above, same reconcile, same locked add/delete.

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
Assignment` fieldname (`enable_prompt_scan`, `output_trap`, `max_tokens`, ...) that
the system seeds onto the row the first time it creates it — an admin can change any
of it afterward in the desk. The system ignores a name already in the 12 predefined
interfaces, so an app can never redefine `security` or `ocr`. Run `bench migrate` (or
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

Next step: [use.md](use.md).
