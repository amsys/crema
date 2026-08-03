# Crema security model

Crema is a security layer first. This page states each guarantee, and each known
limit.

## One entry point

No public function accepts a model name, a provider name, or an API key for an LLM
call. Every call names an interface only. Two System-Manager utility endpoints,
`get_models` and `check_provider`, do take a provider name — they list models and
check connectivity, and neither makes an LLM call. The API key stays inside one
module, `crema/client.py`, in exactly three functions: `_complete` (chat calls),
`_transcribe` (transcription calls), and `_fetch_models` (the model list and
connection check). The key never leaves that module, and never enters redis or any
other cache.

The HTTP surface refuses the internal interfaces `security` and `advanced_ocr`.
They are reachable from Python code only.

## Layer 1 — the scan

The scan is a local regex and unicode check. It runs before any network call. It
reads the prompt, the context, and any `history` turns together, because an attack
can split its wording across the fields.

First the scan checks the raw text for invisible, bidirectional, and control
characters — a soft hyphen or a right-to-left override blocks on its own. Then it
makes the text canonical in four steps, and matches the canonical text against the
injection patterns:

1. It removes the invisible characters that are permitted, so a word split by a
   zero-width joiner becomes one word again.
2. It repairs damaged text, so a phrase that was decoded with the wrong character set
   reads correctly again.
3. It normalizes the text (NFKC), so a fullwidth or other compatibility spelling
   matches the same as its plain form.
4. It changes the text to lowercase ASCII, so a Cyrillic or Greek letter that stands in
   for a Latin letter matches the same as the Latin letter.

A long unbroken base64-like run also blocks: it can hide an instruction from the
patterns. This check reads the raw text, not the canonical text. The scan blocks a call
outright when it finds a match. This is a fail-closed check: the scan blocks an unclear
case, it never passes one through.

Turn the scan off per interface with `enable_prompt_scan` (**Text Scan** in the desk).
It is on by default.

## Layer 2 — the guard

The guard is optional. Turn it on per interface with `enable_llm_guard` (**AI Guard**
in the desk). The guard
sends the prompt and the context together through the `security` interface, and
reads back a risk label: benign, suspicious, or malicious. A malicious label blocks
the call.

The guard fails open: if the guard call errors, or its reply cannot be read, the
original call proceeds. The scan and Frappe's own permission checks are the hard
fence. The guard adds a second opinion, not a second fence.

Two failures are not guard errors, and do not fail open. If the scan (layer 1)
blocks the content inside the guard's own call, the original call is blocked too —
the scan always fails closed, also when the guard is what ran it. If the `security`
interface cannot resolve at all, the call fails loudly with a configuration error.
Crema Settings rejects `enable_llm_guard` at save time unless the `security`
interface has an enabled provider, so this state means the configuration changed
after the save.

## Layer 3 — the output trap

The trap sends a per-call random token along with the request, and asks the model to
echo it back — on its own last line for a plain-text reply, or as a `"_crema"` key for
a JSON reply. A missing token means the model has stopped following crema's own
system prompt (a hijack). A token that appears a second time, somewhere else in the
reply, means the model is echoing that system prompt back (a leak).

Unlike the scan and the guard, the trap does not read the prompt or the context at
all — it only reads what comes back. That makes it the only layer that also covers
`ocr()` and `extract()`, which skip the scan and the guard on purpose because both are
tuned for user prompts and false-positive on arbitrary document text. `transcribe()`
is the one entry point with none of the three layers: it has no system prompt to
protect, so the trap has nothing to arm. The sandbox, the budget check, and the
audit log are its fences.

Set `output_trap` (**Reply Check** in the desk) per interface to one of:

| Value | On a miss |
|---|---|
| `Off` | The system sends no token. No added cost. |
| `Log Only` | The reply comes back as normal; the Crema Log row's `detail` records the miss. |
| `Retry Once` | One fresh attempt, with a new token. A second miss counts as `Block`. |
| `Block` | The call fails with a blocked-call error, and the log records a `Blocked` row. |

A freshly-reconciled row defaults to `Block`. A row whose `output_trap` value is
empty — a row from before the field existed, or a cleared value — runs with the trap
`Off`; pick a value explicitly. If a model turns out to drop the token on ordinary,
non-adversarial replies, step that interface down to `Log Only` — the Crema Log's
`trap:`-prefixed detail rows show how often it happens before you decide.

## The sandbox

Every document read or write inside a call runs as that interface's isolation user —
the **Runs As** field in the desk.
Frappe's own permission engine — roles and User Permissions — checks each access.
Crema adds almost no permission logic of its own on top. The one addition is
`ask(files=[...])`'s explicit read check on each File URL; the one gap is the
private-file read described under [Known limits](#known-limits).

An automation task runs the same way: the whole run — the source read, the plan, the
extraction, and the write — happens as the isolation user. A Document Query
therefore cannot show the model a record that user could not open, and a URL fetch
runs under the same identity as everything after it. See
[automation.md](automation.md).

The isolation user must be a low-privilege, dedicated account. The system refuses
`Administrator` and any user with the `System Manager` role as an isolation user,
because `System Manager` can read a provider's API key. Either choice would remove
the sandbox. The system also refuses a disabled user. These rules apply equally to
Crema Settings' `default_isolation_user` and to a Model Assignment row's own
`isolation_user` override.

The install step creates a default isolation user for you: `crema@<site>` (or
`crema@<site>.localhost` if the site name is not a valid email domain — see
[install.md](install.md)). The
account is enabled, holds only the `Crema User` role, and has no password, so it can
never log in. The install step points `default_isolation_user` at it. It is the
lowest-privilege account Crema ships. Give it more roles or User Permissions only if
you deliberately want the *default* sandbox widened. Never give it `System Manager` —
the `validate_isolation_user` check in the Crema Model Assignment controller rejects
that for this account exactly as for any other.

Every interface with no provider of its own resolves through Crema Settings'
`default_provider`/`default_model`/`default_isolation_user` before falling back per the
chain in [configure.md](configure.md#reference--interfaces-and-the-fallback-chain).
That includes `security` — once `default_provider` is set, `security` counts as
configured even with no row of its own, so `enable_llm_guard` becomes available on
every interface without a separate step.

A fallback walk never weakens a call. The requested interface keeps its own prompt
and its own scan/guard/trap settings; the fallback lends only its provider, model,
isolation user, and billing identity. See
[configure.md](configure.md#reference--interfaces-and-the-fallback-chain).

The switch to the isolation user and back restores the calling browser session
exactly — the session identity, its cache entry, and the request's form data — not
just the calling user's name.

## The audit log

Every call that reaches the provider writes one row to the Crema Log. A cached hit
writes its own row too (`status = Cached`), so hit rate is computable from the log
alone. Some calls write more than one row: a guarded call writes the guard's own
`security` row beside the main row, and a guard that errors, or answers in a shape
the system cannot read, writes one extra row on the main interface recording the
fail-open. An OCR call that escalates to `advanced_ocr` still writes one row, but
its `llm_calls` says 2 — both provider calls are billed under it.

| Field | Notes |
|---|---|
| `interface` | |
| `model` | |
| `user` | The session user. On a row an automation task writes, this is the interface's isolation user — the whole run happens inside the sandbox. |
| `status` | `Success`, `Cached`, `Blocked`, or `Error`. |
| `prompt_sha` | A SHA-256 fingerprint of the full request: interface, model, prompts, context, history, and response format. On an OCR or transcription row: a hash of the file bytes. For correlation only — two identical requests carry the same hash. |
| `duration_ms` | |
| `detail` | A block reason or an error class. Never the prompt or document content. |
| `llm_calls` | Provider calls billed for this row. Usually 1. |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | From the provider's usage response. |
| `cost_usd` | From litellm's computed cost. 0 if the model isn't in litellm's cost map. |

The log never stores the prompt text, the context text, or document content.

On the Crema Log form, `interface`, `user`, `status`, `detail`, `total_tokens`, and
`cost_usd` are always visible. `model`, `provider`, `prompt_sha`, `duration_ms`,
`llm_calls`, `prompt_tokens`, and `completion_tokens` are in a **Diagnostics** section.
Grouping the fields does not change what is stored.

A daily job deletes rows older than `Crema Settings.log_retention_days` (30 by
default).

## Budgets

Each interface has a `monthly_budget_usd` field (in Crema Settings' Use Cases
grid); a row left at 0 uses `Crema Settings.default_monthly_budget_usd` instead, and 0
there too means unlimited. Once the interface's `cost_usd` sum for the current
calendar month reaches its effective budget, the system blocks the next call on that
interface before it reaches the provider — `CremaBudgetError`, logged as a `Blocked`
row.

Each Crema Provider also has its own `monthly_budget_usd` — a combined ceiling across
every interface that uses it. The system checks this in addition to the interface's
own budget: whichever ceiling a call reaches first blocks it. `Crema Log.provider`
records the effective provider on every row (blank on rows from before this field
existed); the provider-level sum comes from that field.

An OCR call that would escalate checks `advanced_ocr`'s own budget first. If that
budget is already spent, the system skips the escalation and returns the first
attempt's text — it does not block the call itself, because its own spend already
happened. The system also skips the escalation when `advanced_ocr` resolves to the
same provider and model as `ocr` — a retry on the same model has no value (see
[configure.md](configure.md#procedure-b--set-the-defaults-the-fast-path)).

The Crema Settings Use Cases grid shows a live **Usage** column — spend to
date if the interface has no budget, otherwise a percentage of its effective budget,
colour-coded (green under 80%, orange 80–99%, red at 100%+). A cache hit never spends,
and is never blocked by a budget.

After a fallback walk, the budget check charges a call to — and the log attributes
it to — the interface that actually serves it, not the one you asked for.

## Secrets

A provider's API key sits in a Password field. The system decrypts it once per
request, inside `crema/client.py` only, and never writes it to redis or any other
cache. `log.redact` scrubs every provider key touched in the request out of error
text before that text reaches a Crema Log row, a task's `last_error`, or the site
error log.

## Known limits

- `automation._fetch` follows HTTP redirects and applies no private-IP or loopback
  block. This is an accepted risk today, because only a System Manager can set
  `source_url`. The **Dry Run** button also reaches it, from a web request rather
  than only from the scheduler. See [ROADMAP.md](../ROADMAP.md) for the planned fix.
- An automation task with a Document Query source reads records that ordinary users
  write. Any user who can edit a field on `source_doctype` can therefore put text in
  front of a model that runs as the interface's isolation user — usually more
  privileged than the writer. The task cannot write outside `target_doctype`, and
  `Update the Records It Read` cannot touch a record outside the batch it read. But the
  *values* the model chooses for the other records in that batch can be steered this
  way. Point a task at a doctype whose content you trust as much as its target.
- The scan is tuned for prompts, not for arbitrary document text, so it has a
  false-positive rate on ordinary records — a soft hyphen pasted from a word
  processor is enough to trip the invisible-character check. A Document Query drops
  the individual records that trip the scan and reports the count in `last_result`,
  rather than failing the whole run. Without that, one odd record would block the
  batch, and five such runs would turn the task off. This per-record drop runs only
  when the interface's `enable_prompt_scan` is on.
- `ocr()`, `extract()`, and `transcribe()` read a private File straight off disk.
  They do not check whether the isolation user could read that File document through
  Frappe's own permission check. The `ask(files=[...])` path does run this check
  correctly. This is a known gap, not yet fixed. An automation source with
  **Attachments** on reaches the same code, but only for files attached to a record the
  query already returned, and it lists those files with a permission-checked query — so
  the fence there is the record, not the file.
- The scan folds many, but not all, look-alike letters. It changes each letter to the
  Latin letters that give its sound, not to the Latin letter it looks like. The two
  agree for most look-alikes, but not for all of them: the Cyrillic letter "es" looks
  like a Latin "c" but gives "s", and the Cyrillic letter "er" looks like a Latin "p"
  but gives "r". A word spelled with one of these letters still evades every pattern.
  To close this needs a confusables table, which Crema does not have.
- The output trap depends on the model actually following the instruction to echo the
  token. A model that drops it on ordinary replies produces a false block on
  `output_trap = Block`. There is no way to tell that apart from a real hijack
  syntactically; the Crema Log is what makes the real rate visible.

## Add a new scan pattern

1. Open `crema/security.py`.
2. Append a `(compiled_regex, reason)` tuple to `_INJECTION_PATTERNS`. Use the flags
   `re.I | re.S` — a line break inside a pattern's gap can defeat a pattern that
   lacks `re.S`. Patterns match the canonical text, so write them in plain lowercase
   ASCII: not a fullwidth spelling, and not a spelling with look-alike letters.
3. Add a matching case to the `CASES` table in `crema/test_security.py`.
