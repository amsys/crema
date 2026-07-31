# Configure Crema

Configure two things before you call Crema: a provider and an interface. A provider
holds a connection to an LLM API. An interface binds a provider, a model, and a
security setting to one named use-case.

Do both from **Crema Settings** — the page Crema opens to by default from the
Framework app switcher, and reachable any time from the Crema workspace sidebar. It
holds a **Providers** panel and a **Model Assignments** grid.

## Procedure A — add a provider

Caution: the system refuses to enable a provider with no API key, unless the base URL
is a local or private address (for example, a local Ollama server).

1. In the **Providers** panel, click **New from Template**. The same button is also on
   the **Crema Provider** list view, if you'd rather start there.
2. Pick a preset: OpenAI, OpenRouter, Groq, Mistral, DeepSeek, or Ollama. The system
   fills in the provider name and base URL — edit either if you're adding a second key
   for the same vendor, since the provider name must be unique.
3. Paste your API key into the **API Key** field.
4. Leave **Enabled** ticked, and leave **Set as default provider** ticked unless you
   already have a default and are only adding a second provider.
5. Click **Create**.

Every enabled provider in the panel shows a live **Connection** status next to
Enabled/Disabled — green with a model count on success, red with the error otherwise
(unauthorized, timeout, unreachable host). It is checked fresh on every page load, not
cached, so a revoked key shows red immediately.

### Crema Provider fields

| Field | Type | Notes |
|---|---|---|
| `provider_name` | Data | Required. Unique. |
| `base_url` | Data | Required. The OpenAI-compatible API base, for example `https://api.openai.com/v1`. |
| `api_key` | Password | Required to enable, unless the base URL is local or private. |
| `enabled` | Check | Off by default. |
| `timeout_seconds` | Int | 60 by default. Raise this for a local or self-hosted provider, for example Ollama on CPU. |
| `monthly_budget_usd` | Currency | 0 (default) means unlimited. A combined ceiling across every interface using this provider — see [security.md](security.md#budgets). |

## Procedure B — set the defaults (the fast path)

Every interface works as soon as three fields are set, above the Model Assignments
grid:

1. **Default Provider** — the provider from Procedure A. Already filled in if you left
   "Set as default provider" ticked there.
2. **Default Model** — a model from that provider. This field stays locked until a
   Default Provider is set, then lists that provider's models. If you clear it, or the
   provider changes and it no longer matches, the system warns on save but still lets
   you save — fill it in before you actually call an interface that needs it.
3. **Default Isolation User** — pre-filled on install with a dedicated, unprivileged
   account (`crema@<site>`, no password, `Crema User` role only) created for exactly
   this. Leave it unless you want a different low-privilege account for the default.

Save. Every one of the 12 predefined interfaces now resolves through these three
values, including `security` — turning on `enable_llm_guard` anywhere no longer needs
`security` configured with its own provider first.

## Procedure C — override one interface

The **Model Assignments** grid always lists every interface, one row each, by its
human-readable name (`Default`, `Translation`, `OCR`, and so on — see the reference
table below for the full, fixed list and each row's underlying key). Nothing to
create, and no row can be added or removed: the grid has no add/delete controls, and
the system reconciles it to that exact list every time you save Crema Settings,
including on install and migrate.

The grid stays collapsed as long as every row is pure default (nothing set in
Provider, Model, or Isolation User), and opens automatically once any row has an
override. A blank cell in Provider, Model, or Isolation User always means "use the
matching Default from Procedure B" — fill in a cell only to point that one interface
somewhere else.

Only **Provider** and **Model** are editable directly in the grid row. Everything
else — Isolation User, the monthly budget, temperature, cache TTL, the security
toggles, the system prompt — lives behind the pencil (**Edit**) icon at the end of
the row, which opens that interface's full field set.

Warning: pick a low-privilege isolation user, for a row override same as for the
default. The system refuses `Administrator` and any user with the `System Manager`
role, because `System Manager` can read the provider's API key. Either choice would
remove the sandbox.

1. In the row for the interface you want to override, pick a **Provider** different
   from the default directly in the grid.
2. Pick a **Model**. This column stays empty until this row has its own provider; the
   list is fetched live from that provider.
3. Click the row's **Edit** icon to set the **Isolation User**, if this interface needs
   a different one from the default. A row that sets a Provider without its own
   Isolation User still uses the Default Isolation User.
4. Click **Save** (the normal form save — this is one document, Crema Settings).

**Reset Assignments**, in the grid's toolbar, blanks every row's Provider, Model,
Isolation User, budget, temperature, cache TTL and system prompt in one action — every
interface falls back to the Defaults above. It only clears the fields on screen; the
form is left unsaved so you can review before clicking Save (or discard by reloading
the page).

The **Usage** column shows this month's spend against each interface's effective
budget (its own, or the Default Monthly Budget) — a grey dollar amount if unbudgeted,
otherwise a percentage: green under 80%, orange 80–99%, red at 100% or over. See
[security.md](security.md#budgets).

### Crema Model Assignment fields (one row in the grid)

| Field | Type | Notes |
|---|---|---|
| `interface_label` | Data | Read-only. The human-readable name shown in the grid's Interface column (see the reference table below). |
| `interface` | Data | Read-only. The key passed to `ask()` and friends — the full set is reconciled on every save, not created by hand. |
| `provider` | Link | Blank uses Crema Settings' Default Provider; if that's blank too, the interface falls back per the chain below. |
| `model` | Autocomplete | Blank uses the Default Model. Fetched from this row's own provider once one is set, or from the Default Provider if this row leaves Provider blank too. |
| `usage` | Data | Read-only, never saved. Painted live from this month's Crema Log spend each time the page loads. |
| `isolation_user` | Link (User) | Not `Administrator`. Not a `System Manager`. Blank uses the Default Isolation User. An effective provider (this row's or the default's) always needs an effective isolation user (this row's or the default's). |
| `monthly_budget_usd` | Currency | 0 (default) means "use the Default Monthly Budget"; 0 there too means unlimited. See [security.md](security.md#budgets). |
| `temperature` | Float | 0 by default. |
| `max_tokens` | Int | Caps the response length. 0 (default) leaves the provider's own default untouched. |
| `cache_ttl` | Int | Seconds to cache a response. 0 turns caching off. |
| `enable_prompt_scan` | Check | On by default. Layer 1 — the local scan. |
| `enable_llm_guard` | Check | Off by default. Layer 2 — the LLM guard. Needs an effective `security` provider (its own row, or Default Provider). |
| `output_trap` | Select | `Block` by default (a freshly-reconciled row; an existing row from before this field was added stays at `Off` until you pick a value — see [security.md](security.md#layer-3--the-output-trap)). Layer 3 — the output trap. `Off` / `Log Only` / `Retry Once` / `Block`. |
| `system_prompt` | Long Text | Fills in from a built-in default if you leave it empty. |

Set `cache_ttl` on the `view` row to make a repeated desk "Ask Crema" list-view request
free: the exact same request on the same doctype is served from cache, with no provider
call, until the TTL expires.

### Crema Settings fields (the rest of the page)

| Field | Type | Notes |
|---|---|---|
| `default_provider` | Link | Used by any row that leaves Provider blank. |
| `default_model` | Autocomplete | Used by any row that leaves Model blank. Locked until Default Provider is set; lists that provider's models. |
| `default_isolation_user` | Link (User) | Used by any row that leaves Isolation User blank. Seeded on install/migrate — see [install.md](install.md). |
| `default_monthly_budget_usd` | Currency | Used by any row that leaves its own monthly budget at 0. 0 here too means unlimited. |
| `log_retention_days` | Int | 30 by default. How long a Crema Log row survives before the daily cleanup job deletes it. |

## Reference — interfaces and the fallback chain

| Interface | Grid label | Purpose | Fallback |
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
| `view` | View | Turn a prompt into a List/Report/Kanban view for the desk UI | `complex` |
| `transcribe` | Transcribe | Speech-to-text via `crema.transcribe()` | none — a transcription call cannot fall back to a chat model |

If an interface has no provider of its own, the system tries Crema Settings' Default
Provider first. Only if that is also blank does it try the next interface in its
fallback chain. If nothing resolves this way (including `simple`, the end of every
chain), the system raises `CremaConfigError`.

A fallback only supplies the provider, model, and isolation user. The system keeps
the prompt of the interface you actually asked for — `view`, `transform`, and
`extraction` each expect strict JSON back, and the fallback's own prompt would
produce plain text the caller cannot parse.

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

`prompt` and `fallback` are required; every other key is a `Crema Model Assignment`
fieldname (`enable_prompt_scan`, `output_trap`, `max_tokens`, ...) seeded onto the row
the first time it's created — an admin can change any of it afterward in the desk. A
name already in the 12 built-in interfaces is ignored, so an app can never redefine
`security` or `ocr`. Run `bench migrate` (or restart) so the new row appears.

**In crema itself**, for a new built-in interface that talks to a provider the same
way `ask()` already does (a chat completion):

1. Open `crema/interfaces.py`.
2. Add the name to the `PREDEFINED` list.
3. Add a default system prompt to `DEFAULT_PROMPTS`.
4. If the interface should degrade to another one, add it to `FALLBACKS`.

No other code change is needed for that shape. An interface calling a genuinely
different provider endpoint — `transcribe` (`litellm.transcription`, not
`litellm.completion`) is the precedent — additionally needs its own function next to
`client._complete` and its own entry point in `crema/api.py` (see `transcribe()`),
since it has no system prompt to run through `_resolve`'s usual chat-message path. It
still skips `DEFAULT_PROMPTS` and usually `FALLBACKS` (a transcription call cannot
fall back to a chat model, an OCR call cannot fall back to a transcription one — each
non-chat interface's fallback chain, if any, stays within its own kind).

Next step: [use.md](use.md).
