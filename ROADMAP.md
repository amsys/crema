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
  step instead of upserting them (`dry_run` already stops at that point and renders
  the rows, so the new work is persistence and approval, not the pipeline), plus a
  per-task confidence floor now that `ocr()`/`extract()`'s `confidence` has a
  consumer. Submitting a document a model derived stays out of scope — the drafts are
  the point, a human clicking Submit is the approval.
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

- **Streaming responses** over `frappe.publish_realtime`. This conflicts with layer 3
  (the output trap): the trap can only validate its nonce once the response is
  complete, so naive token-by-token streaming would ship unverified text early or not
  really stream at all. The shape that avoids this: an opt-in `stream_to=` channel on
  `ask()` that skips the response cache and is only permitted when the interface's
  `output_trap` is `Off` or `Log Only`.

#### Model and provider plumbing

- **Permission-fenced tool calling.** Let an interface call back into
  `frappe.get_list`/`frappe.get_doc` under its isolation user, reusing the same
  sandbox fence `ask()` already uses for files.
- **Provider health checks and automatic fallback chains**, beyond the static
  `FALLBACKS` walk `client._resolve` already does. `client.check_connection` (the
  Providers panel's live status check) is the existing half — a scheduled version of
  the same check, feeding provider `enabled` automatically, is the missing half.

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
