### Roadmap

Themed, not priority-ordered — pick by what you need, not by position in the list.
Security hardening has its own list, in [docs/security.md](docs/security.md#planned-hardening)
— it lives there because each item is a gap in a guarantee that page already states.

#### Automation

- **A `Propose Only` action, and a confidence floor.** Every other surface in crema
  proposes and lets a human apply — `transform()` returns a diff the desk dialog
  applies, `extract()` returns records the caller creates. Automation is the one
  surface that writes unattended: a task is either fully trusted
  (`Create or Update Records`) or writes nothing (`No Changes`), with no middle
  setting — exactly where anything touching money sits. The shape: a fourth action
  that runs the pipeline through EXTRACT and parks the rows for an approve-or-discard
  step instead of writing them (`dry_run` already stops at that point and renders
  the rows, so the new work is persistence and approval, not the pipeline), plus a
  per-task confidence floor now that `ocr()`/`extract()`'s `confidence` has a
  consumer. A parked row carries a stable identity (task + source document +
  `prompt_sha`), so a run that repeats after a crash finds its earlier proposal
  instead of filing the same one twice. Submitting a document a model derived stays
  out of scope — the drafts are the point, a human clicking Submit is the approval.
- **Child tables in a Document Query.** `_source_fields` keeps to `data_fieldtypes`,
  so a record travels to the model without its own line items — no item rows on a
  Sales Invoice, no components on a BOM. That blocks the stock/manufacturing reports
  a `No Changes` task looks made for (shrinkage checks, BOM audits, stock-loss
  summaries). Fix: serialise each child row through the same explicit-fieldlist fence
  `_source_fields` already applies to the parent, capped per record so one long
  invoice doesn't eat the whole content budget alone.
- **Related-record context for a Document Query.** `_read_documents` reads one
  doctype's own fields, so a reconciliation an ERP wants — an invoice against its
  order and receipt, a payment against the invoices it clears — has no way to put the
  other side of the comparison in front of the model. Worth building
  [permission-fenced tool calling](#model-and-provider-plumbing) first: give an
  interface a callback into `frappe.get_list` under its isolation user and a task gets
  this for free, with no new source field — the plan names what to look up, rather
  than the task configuring a join up front.

#### Desk experience

- **Streaming responses** over `frappe.publish_realtime`, in two shapes. The default
  shape is buffer-then-release: the full reply arrives, the guardrail onion runs
  (the trap can only validate its nonce, and masking can only swap its tokens back,
  once the response is complete), and the desk then renders progressively — a
  two-to-four-second wait is fine in an ERP form, and no guarantee is weakened.
  True token-by-token streaming stays a separate opt-in `stream_to=` channel on
  `ask()` that skips the response cache and is only permitted when the Reply Check
  guardrail is `Off` or `Log Only` for the interface; if a chat-style surface ever
  wants it with checks on, the shape is a sliding-window scan with a mid-stream kill
  and retract, which is real new machinery — not before someone needs it.

#### Guardrails

- **Per-source reply checks.** The trap arms one nonce per call. Arming a distinct
  nonce per context channel — system prompt, document context, file text — would make
  a sprung trap name the channel that was compromised, not just the fact of the
  compromise. Cheap on the arming side; the cost is prompt space and a stricter echo
  instruction, so it needs measuring against real models' echo reliability first.

#### Model and provider plumbing

- **Permission-fenced tool calling.** Let an interface call back into
  `frappe.get_list`/`frappe.get_doc` under its isolation user, reusing the same
  sandbox fence `ask()` already uses for files. Three constraints are part of the
  design, not options on it: the tools stay read-only — a window that contains
  document text contains untrusted text, and a read-only turn is the one structural
  defence against injection that does not depend on a detector working, so a write
  tool would first need a taint model that downgrades the turn; the AI Guard's
  highest-value position is judging the *proposed tool call* against the user's
  request (a small structured object is hard to talk around, unlike free text); and
  every tool result is truncated before it enters the context, so one `get_list` on a
  fat doctype cannot eat the window and the budget alone.
- **Background execution for long completions.** Every `ask()` today runs the
  provider call synchronously inside the web request, so a slow completion occupies a
  web worker for its whole duration. An opt-in enqueue path — the worker shape
  automation already uses, with the result delivered over `frappe.publish_realtime` —
  frees the worker and pairs naturally with the streaming item above.
- **Provider health checks and automatic fallback chains**, beyond the static
  `FALLBACKS` walk `client._resolve` already does. `client.check_connection` (the
  Providers panel's live status check) is the existing half — a scheduled version of
  the same check, feeding provider `enabled` automatically, is the missing half.

#### Operations

- **A kill switch.** No single flag turns crema off today — the nearest levers are
  per-provider `enabled` and a spent budget, neither of which is a switch. One check —
  a `Crema Settings` field or a `site_config` key, read at every `ask()` entry, in
  `automation.on_doc_event`, and in the scheduler tick — that disables every LLM
  feature on the site at once. A production incident needs seconds, not a deploy.
- **Per-user spend ceilings.** The monthly budgets are site-wide, summed per interface
  and per provider; the only per-user limit is the HTTP surface's 60-calls-per-hour
  rate limit, which caps call count, not cost. `log.month_spend` gaining a user
  dimension gives a per-user monthly ceiling beside the existing two.

#### Retrieval

- **Embeddings and document search.**

#### Surface

- **An HTTP endpoint for `ocr()`.** `ask_api`/`extract_api`/`transform_api` cover
  `ask`/`extract`/`transform`, but a caller who only wants OCR text has no HTTP path.
  Small — `extract_api` is the template to mirror.

Explicitly, and permanently, out of scope: turning `crema` into a chatbot — a chat
*surface* with its own session store, turn management, or UI. `ask(..., history=...)`
lets a caller hand crema its own transcript for one call; crema still owns exactly one
system prompt per interface and persists no conversation of its own.

Considered and rejected: demoting the Text Scan to telemetry-only on the argument that
regex cannot catch a determined attacker. The scan is the cheap, deterministic,
fail-closed pre-filter, not the detector of record — that division of labour is the
design, and a site that wants telemetry-only sets the row to `Log Only`; the ladder
already exists per row.
