# Crema security model

Crema is a security layer first. This page states each guarantee, and each known
limit.

## One entry point

No public function accepts a model name, a provider name, or an API key. Every call
names an interface only. The API key stays inside one function,
`crema.client._complete`. It is never passed to, or read by, any other code path.

## Layer 1 — the scan

The scan is a local regex and unicode check. It runs before any network call. It
reads the prompt and the context together, because an attack can split its wording
across the two fields. Before it checks a piece of text against a pattern, it
normalizes that text (NFKC), so a fullwidth or other compatibility spelling of an
attack phrase is caught the same as its plain ASCII form. The scan blocks a call
outright when it finds a match. This is a fail-closed check: an unclear case is
blocked, not passed through.

Turn the scan off per interface with `enable_prompt_scan`. It is on by default.

## Layer 2 — the guard

The guard is optional. Turn it on per interface with `enable_llm_guard`. The guard
sends the prompt through the `security` interface, and reads back a risk label:
benign, suspicious, or malicious. A malicious label blocks the call.

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
a JSON reply. The token comes back missing when the model has stopped following
crema's own system prompt (a hijack); it comes back a second time, somewhere else in
the reply, when the model is echoing that system prompt back (a leak).

Unlike the scan and the guard, the trap does not read the prompt or the context at
all — it only reads what comes back. That makes it the only layer that also covers
`ocr()` and `extract()`, which skip the scan and the guard on purpose because both are
tuned for user prompts and false-positive on arbitrary document text.

Set `output_trap` per interface to one of:

| Value | On a miss |
|---|---|
| `Off` | No token is sent. No added cost. |
| `Log Only` | The reply is returned as normal; the miss is recorded in the Crema Log row's `detail`. |
| `Retry Once` | One fresh attempt, with a new token. A second miss is treated as `Block`. |
| `Block` | The call fails with a blocked-call error, logged as a `Blocked` row. |

A freshly-reconciled row defaults to `Block`. If a model turns out to drop the token
on ordinary, non-adversarial replies, step that interface down to `Log Only` — the
Crema Log's `trap:`-prefixed detail rows show how often it happens before you decide.

## The sandbox

Every document read or write inside a call runs as that interface's isolation user.
Frappe's own permission engine — roles and User Permissions — checks every access.
Crema adds no permission logic of its own on top.

The isolation user must be a low-privilege, dedicated account. The system refuses
`Administrator` and any user with the `System Manager` role as an isolation user,
because `System Manager` can read a provider's API key. Either choice would remove
the sandbox. This rule applies equally to Crema Settings' `default_isolation_user` and
to a Model Assignment row's own `isolation_user` override.

The install step creates a default isolation user for you — `crema@<site>`, enabled,
no password set (so it can never log in), holding only the `Crema User` role — and
points `default_isolation_user` at it. It is the lowest-privilege account Crema ships:
give it more roles or User Permissions only if you deliberately want the *default*
sandbox widened, and never `System Manager` (`CremaModelAssignment.validate_isolation_user`
rejects that for this account exactly as it would for any other).

Every interface with no provider of its own now resolves through Crema Settings'
`default_provider`/`default_model`/`default_isolation_user` before falling back per the
chain in [configure.md](configure.md#reference--interfaces-and-the-fallback-chain).
That includes `security` — once `default_provider` is set, `security` counts as
configured even with no row of its own, so `enable_llm_guard` becomes available on
every interface without a separate step.

The switch to the isolation user and back restores the calling browser session's
identity exactly, not just the calling user's name. An earlier version restored the
user only, which corrupted the session cache and logged the caller out on every call.

## The audit log

Every call that reaches the provider writes one row to the Crema Log. A cached hit
writes its own row too (`status = Cached`), so hit rate is computable from the log
alone. An OCR call that escalates to `advanced_ocr` still writes one row, but its
`llm_calls` says 2 — both provider calls are billed under it.

| Field | Notes |
|---|---|
| `interface` | |
| `model` | |
| `user` | The real session user, not the isolation user. |
| `status` | `Success`, `Cached`, `Blocked`, or `Error`. |
| `prompt_sha` | A SHA-256 hash of the prompt, for correlation only. |
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

Rows older than `Crema Settings.log_retention_days` (30 by default) are deleted daily.

## Budgets

Each interface has a `monthly_budget_usd` field (in Crema Settings' Model Assignments
grid); a row left at 0 uses `Crema Settings.default_monthly_budget_usd` instead, and 0
there too means unlimited. Once an interface's effective budget is reached by its
`cost_usd` sum for the current calendar month, the next call on that interface is
blocked before it reaches the provider — `CremaBudgetError`, logged as a `Blocked` row.

Each Crema Provider also has its own `monthly_budget_usd` — a combined ceiling across
every interface that uses it. This is checked in addition to the interface's own
budget: whichever is reached first blocks the call. `Crema Log.provider` records the
effective provider on every row (blank on rows logged before this field existed), which
is what the provider-level sum is computed from.

An OCR call that would escalate checks `advanced_ocr`'s own budget first. If that
budget is already spent, the escalation is skipped and the first attempt's text is
returned — the call itself is not blocked, because its own spend already happened.

The Crema Settings Model Assignments grid shows a live **Usage** column — spend to
date if the interface has no budget, otherwise a percentage of its effective budget,
colour-coded (green under 80%, orange 80–99%, red at 100%+). A cache hit never spends,
and is never blocked by a budget.

After a fallback walk, a blocked call is charged to, and logged against, the
interface that actually serves it — not the one you asked for. This matches how the
audit log already attributes a fallback-served call.

## Secrets

A provider's API key sits in a Password field. The system decrypts it once per
request. It is never written to redis or any other cache.

## Known limits

State plainly, not defensively:

- `automation._fetch` follows HTTP redirects and applies no private-IP or loopback
  block. This is an accepted risk today, because only a System Manager can set
  `source_url`. See [ROADMAP.md](../ROADMAP.md) for the planned fix.
- `ocr()` and `extract()` read a private File straight off disk. They do not check
  whether the isolation user could read that File document through Frappe's own
  permission check. The `ask(files=[...])` path does run this check correctly. This
  is a known gap, not yet fixed.
- The scan's NFKC normalization does not fold homoglyphs — a Cyrillic "о" standing in
  for a Latin "o" still evades every pattern. That needs a confusables table, not
  added here.
- The output trap depends on the model actually following the instruction to echo the
  token. A model that drops it on ordinary replies produces a false block on
  `output_trap = Block`. There is no way to tell that apart from a real hijack
  syntactically; the Crema Log is what makes the real rate visible.

## Add a new scan pattern

1. Open `crema/security.py`.
2. Append a `(compiled_regex, reason)` tuple to `_INJECTION_PATTERNS`. Use the flags
   `re.I | re.S` — a line break inside a pattern's gap can defeat a pattern that
   lacks `re.S`. Patterns match text AFTER NFKC normalization, so write them against
   the normalized (plain ASCII-ish) spelling of the phrase, not a fullwidth one.
3. Add a matching case to the `CASES` table in `crema/test_security.py`.
