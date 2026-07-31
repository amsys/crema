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
