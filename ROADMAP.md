### Roadmap

Themed, not priority-ordered — pick by what you need, not by position in the list.

#### Recently shipped

- An apply-UI for `transform`'s proposed diffs: a robot button on Desk form views opens
  a dialog, proposes a diff (`transform_api`), and applies it into the open form for the
  user to save — no more hand-rolling the apply step yourself.
- `extract()` now proposes several records from one file, not just one — the model
  decides how many records a document holds; a caller no longer ticks a checkbox up
  front to say so.
- App-registered interfaces: any installed app can add a new interface name via the
  `crema_interfaces` hooks.py key (`{name: {"prompt", "fallback", ...}}`, or a dotted
  path string to one — the same lazy-resolution convention Frappe's own hooks already
  use for `after_install`/scheduler jobs, so the app's own prompt module is only
  imported on first use, not at every process boot), read by `interfaces.names()` /
  `interfaces.app_interfaces()` and reconciled onto Crema Settings by
  `CremaSettings._reconcile_assignments` exactly like `PREDEFINED` always was. An app
  names a prompt and a fallback — never a model, provider, or key; those stay
  admin-owned in the desk, same as every core interface.
- `ask(files=[...])` now also accepts `(bytes, mime)` tuples alongside Frappe File
  URLs — for vision content a caller already holds and has permission-checked itself
  (a bot photo with no Frappe File behind it, for example), not just a File on disk.
- A per-interface `Max Tokens` field on `Crema Model Assignment`, carried into
  `client._complete`'s `max_tokens` kwarg when set (0, the default, leaves the
  provider's own default untouched).
- `ask(..., history=[...])` lets a caller pass prior `{"role", "content"}` turns,
  inserted between the interface's system prompt and the current call's user turn —
  `security.scan` covers the whole joined history, not just the latest turn. See the
  closing line below for what this does and doesn't mean for the chatbot non-goal.
- A `transcribe` interface next to `ocr()`: `crema.transcribe(file, language=...)`
  calls `litellm.transcription` the same way `_complete` calls `litellm.completion` —
  no system prompt, no fallback (a transcription call cannot fall back to a chat
  model), still budget-checked and logged to Crema Log. Closes the gap the "Model and
  provider plumbing" section used to describe: a consuming app that also needs
  speech-to-text no longer has to read `Crema Provider` directly for its key.
- `crema.health(interface, live=True)` / `crema.is_configured(interface)`: a
  below-System-Manager status check. Deliberately not `@frappe.whitelist()`'d and
  applies no role check of its own — the consuming app's dashboard/health-check button
  applies whatever gate it needs, then calls in-process. Reports provider, model, live
  reachability (now split from "ok" — see `client.check_connection`'s new `reachable`
  key, so a down endpoint reads differently from a rejected key), and month-to-date
  spend/budget. Never returns or logs an `api_key`.

#### Desk experience

- Streaming responses over `frappe.publish_realtime`. This conflicts with layer 3 (the
  output trap): the trap can only validate its nonce once it has the complete response
  body, so naive token-by-token streaming would either ship unverified text to the
  browser before the trap has run, or not really stream at all. The shape that avoids
  this: an opt-in `stream_to=` channel on `ask()` that skips the response cache and is
  only permitted when the interface's `output_trap` is `Off` or `Log Only` — a `Block`/
  `Retry Once` interface keeps its current all-or-nothing response.

#### Automation

- A file source. `source_type` is `URL` or `Document Query` today, so the ERP ingestion
  every site actually wants — a supplier invoice PDF, an expense receipt photo, a bank
  statement, a certificate of insurance, a signed delivery note, a supplier price
  list — has no path into a task at all. Most of it is
  already built: `api.extract(doctype, file_url)` does the whole file-to-records step
  (OCR, text-vs-scanned PDF, multi-record splitting, `_filter_diff` against the target
  meta) and returns its own `confidence`. The shape that reuses the most is a `File
  Query` source — `_read_documents` verbatim over the `File` doctype, same filters, same
  `source_limit`, same `incremental` watermark, same `get_list` read fence — whose
  per-file step calls `extract()` instead of serialising the row to JSON. PLAN and
  EXTRACT drop out of that path entirely, since `extract()` plans against the target
  doctype's metadata itself; what `_upsert` still needs from the plan is `match_fields`,
  so the same invoice attached twice updates one record instead of importing two.
  One prerequisite, and it is not optional: `_ocr._load_bytes` resolves a File URL with
  `frappe.utils.file_manager.get_file()`, which never calls `check_permission()` — its
  docstring's claim that "private-file permissions apply" is not true today, and
  `test_ocr.test_private_file_url_content_is_read_regardless_of_isolation_user_permission`
  pins that. Bounded while a System Manager hands over the URL by hand; a task reading
  whatever a `File` query returns, under an isolation user, would make the sandbox fence
  advertised but not enforced. That check lands first, or this feature does not land.
- A `Propose Only` action, and a confidence floor. Every other surface in crema proposes
  and lets a human apply: `transform()` returns a diff the desk dialog applies into an
  open form, `extract()` returns records the caller creates under its own permissions.
  Automation is the one surface that writes unattended, so a task is either fully
  trusted (`Upsert Records`) or writes nothing at all (`Report Only`) — there is no
  middle setting, which is exactly where anything touching money sits. The shape: a
  fourth action that runs the pipeline to the end of EXTRACT and parks the rows instead
  of upserting them, plus an approve-or-discard step that replays `_upsert` on rows
  already validated. `dry_run` is most of the read path already — it stops at precisely
  this point and renders the rows for a dialog — so the new work is persistence and the
  approve step, not the pipeline. A per-task confidence floor rides along: `ocr()` and
  `extract()` both return a `confidence` this path discards, so a task cannot say "write
  it when you are sure, park it when you are not". Note what this deliberately does not
  unlock: `_upsert` calls `doc.save()` and nothing else, and submitting a document a
  model derived is not on this list — the drafts are the point, and a human clicking
  Submit is the approval.
- Child tables in a document query. `_source_fields` keeps to `data_fieldtypes`, so a
  record travels to the model without its own line items — a Sales Invoice with no item
  rows, a BOM with no components, a Purchase Receipt with no received quantities, a
  Stock Reconciliation with no per-item differences. That silently blocks the stock and
  manufacturing reports a `Report Only` task looks made for: flag receipts whose
  received quantity keeps landing short of what was ordered (the shrinkage /
  short-shipment check), audit BOMs for components that are disabled, mis-priced, or in
  the wrong UOM, summarise which items and warehouses keep losing stock across stock
  reconciliations, cluster quality-inspection failures by their readings. The fix is
  contained: serialise each child row through the same explicit-fieldlist fence
  `_source_fields` already applies to the parent (per child meta — permlevel and
  Password fields stay out), and cap the child rows per record so one 500-line invoice
  does not eat the whole `_EXTRACT_CONTENT_CHARS` budget alone. Until then the planner
  and the extractor disagree: `api._meta_summary` shows the planner child-table fields,
  so an `Update Source Records` plan can name child fields of the very doctype whose
  serialisation omits them — a plan that validates, then extracts nothing.
- Related-record context for a document query. `_read_documents` reads exactly one
  doctype's own fields, so every reconciliation an ERP wants — an invoice against its
  order and receipt, a payment against the invoices it clears, a delivery against its
  sales order, a work order's actual consumption against its BOM, a stocktake against
  the ledger it should reconcile — has no way to put the other side of the comparison
  in front of the model. The child-table gap above compounds it: most of what needs
  comparing lives in the line items on both sides. Whatever supplies it has to keep `_source_fields`' explicit field list (`["*"]`
  would hand permlevel fields on a `CORE_DOCTYPES` doctype straight to a provider) and
  stay on `get_list` under the isolation user. The existing half is the permission-fenced
  tool calling item below: give an interface a callback into `frappe.get_list` under its
  isolation user and an automation task gets this for free, with no new source field at
  all — the plan names what to look up, rather than the task configuring a join up
  front. Worth building that one first and seeing what is genuinely left over.

#### Model and provider plumbing

- Permission-fenced tool calling: let an interface call back into `frappe.get_list` /
  `frappe.get_doc` under its isolation user, reusing the same sandbox fence `ask()`
  already uses for files.
- Provider health checks and automatic fallback chains, beyond the static `FALLBACKS`
  walk `client._resolve` already does. `client.check_connection` (the Providers panel's
  live status check) is the existing half of this — a scheduled version of the same
  check, feeding provider `enabled` automatically, is the missing half.

#### Retrieval

- Embeddings and document search.

#### Surface

- An HTTP endpoint for `ocr()` — today `ask_api`/`extract_api`/`transform_api` cover
  `ask`/`extract`/`transform`, but a caller who only wants OCR text has no HTTP path.
  Small: `extract_api` is the template to mirror.

#### Hardening

- SSRF egress controls for `automation._fetch`: it follows redirects with no
  private-IP/loopback block. Accepted risk today because `source_url` is only
  settable by a System Manager, but a deny-list (or an allow-listed egress proxy)
  would close it properly.
- Per-app scan patterns — the `security._INJECTION_PATTERNS` mirror of the
  `crema_interfaces` hook. `scan()` must stay frappe-free (no hooks lookup inside it),
  so this belongs one level up: a caller-side merge that widens what gets passed to
  `scan()`, the same shape `_scan_context` already uses for `history`.
- An output-side content filter hook. `log.redact` only scrubs a provider's own
  `api_key` out of error text, never the model's reply; a consuming app that needs a
  PII/secret-term filter on every response runs its own regex layer today because
  crema has no equivalent.

- **Layer 1 evasion resistance.** `security.scan` is ordered-list, first-match-wins
  regex over ~20 literal English phrasings, run once after NFKC normalization. It
  catches textbook injection phrasing and stops there — a homoglyph, a spaced-out
  word, or a paraphrase currently reaches layer 2 alone. Ideas below, ordered by value
  per unit of false-positive risk added; none of this is implemented yet.

  1. **Homoglyph folding.** NFKC does not fold a Cyrillic `о` to a Latin `o`, so
     `іgnore prevіous іnstructions` passes every pattern unchanged. Add a Unicode
     TR39 confusables-skeleton pass alongside the existing NFKC step — `scan()`'s own
     docstring already names this as the known remaining gap.
  2. **Separator squeezing.** `i g n o r e`, `ig-nore`, `i.g.n.o.r.e`, and
     `ignore***previous` all defeat a literal pattern. Re-run the pattern loop over a
     second copy of the text with runs of whitespace/punctuation between letters
     collapsed. This manufactures its own false positives (an ordinary sentence like
     "…ignore. Previous instructions were unclear…" would now match) — needs item 6
     before it ships un-flagged.
  3. **Leet folding.** `1gn0re pr3v10us` — a small digit-to-letter fold, applied only
     to the squeezed copy from item 2, not the raw text (keeps the false-positive
     surface to one extra pass, not two).
  4. **Bounded decode-and-rescan.** An 80-character pure-base64 run is blocked today;
     a 40-character one that decodes to "ignore previous instructions" is not. Decode
     base64/hex/percent/HTML-entity/ROT13 candidates once (depth 1, no recursive
     decoding), rescan each decoded candidate, block on a hit. Only decode runs whose
     output comes back mostly printable ASCII, so ordinary non-text data can't
     decode into an accidental match.
  5. **Score instead of first-match-wins.** The real remaining gap is paraphrase —
     "set aside what you were told before and follow this instead" matches nothing
     above. Keep every existing literal pattern as an unconditional hard block, and
     add a second, scored layer over independent signals (an override verb, a
     reference to prior instructions, a role/persona assignment, a secrecy request)
     that blocks once their combined weight crosses a threshold. Highest ceiling,
     also the highest false-positive risk of anything on this list.
  6. **A false-positive corpus, and a shadow mode, before 1-5 ship live.** None of the
     above belongs in a fail-closed layer without a way to measure what it breaks.
     Build a fixture corpus of ordinary business text (quotes, meeting minutes,
     emails, invoices) that must stay clean, and give each new pattern/signal a
     shadow flag that logs a "would have blocked" row to Crema Log instead of
     raising — the same Log Only → Block ladder `output_trap` already uses for layer
     3. Every new pattern ships in shadow first and is promoted to a live block only
     on evidence from that log.
  7. **Language coverage.** Every existing pattern is English phrasing; an injection
     written in French, Hindi, or any other language reaches layer 2 alone today,
     which fails open by design. A static, per-language pattern table — `scan()`
     stays frappe-free (no `frappe.local.lang` lookup), so this is a data addition,
     not an architecture change.
  8. **Non-goal:** matching an LLM classifier's paraphrase recall in regex. Layer 1
     is meant to be the cheap, deterministic, offline pre-filter; catching genuine
     semantic paraphrase is layer 2's job. Trading layer 1 false positives for recall
     layer 2 already provides would be a bad trade, not a hardening win.

Explicitly, and permanently, out of scope: turning `crema` into a chatbot — a chat
*surface* with its own session store, turn management, or UI. `ask(..., history=...)`
lets a caller hand crema its own transcript for one call; crema still owns exactly one
system prompt per interface and persists no conversation of its own.
