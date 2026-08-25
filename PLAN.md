# PLAN

This is crema's single planning file. It replaces ROADMAP.md, and it absorbs the
working notes that lived outside the repo: the gateway split plan, the Frappe AI
ecosystem survey, the experimental-branch design note, and the v16→v17 findings.
Detailed security gaps keep their home in
[docs/security.md — Planned hardening](docs/security.md#planned-hardening), because
each one annotates a guarantee that page states. This file decides and orders.

## The line that everything follows

Crema keeps every check that needs Frappe context — records, fields, users,
permissions, interfaces — or model cooperation, a contract written into the system
prompt. A gateway (litellm-proxy) owns every check that works on bare text, and
every ceiling that meters infrastructure. A site gets the gateway layer by pointing
a Crema Provider's `base_url` at its proxy, with no crema change
([docs/security.md — Behind a gateway](docs/security.md#behind-a-gateway)).

This line already removed the AI Guard and the per-provider budget, froze the Text
Scan's scope, and added the proxy cost-header and caller-identity pass-through.
Each Included and Rejected call below is the same line, applied again.

## What an integrating programmer gets today

The strong parts, seen from a consuming app: one import (`from crema import ask`),
no provider name or key in app code, typed exceptions, a supported test bootstrap
(`crema.testing`), a hook for a site's own guardrail (`crema_guardrails`), an
interface registry for an app's own use cases (`crema_interfaces`), per-interface
and per-user budgets, and one audit row per call that never stores content. The
identity to build on, and the thing to be remembered for: **propose, never write** —
every surface returns a proposal that a human or the caller applies.

The gaps that ordered the list below: automation is the one surface that writes
unattended; a consuming app has no hook on the model's reply; a cached reply is not
scoped to a user; and a slow completion holds a web worker for its whole duration.

## Included

Ordered by benefit to a programmer integrating crema.

### The integration surface

1. **An output-side content filter hook.** `log.redact` scrubs only a provider's
   own API key out of error text, never the model's reply. A consuming app that
   needs a PII or secret-term filter on responses runs its own regex layer today.
   Give it the same shape `crema_guardrails` has on the input side. This is also
   the future home for the markdown-image exfiltration defence named in the
   security page's Known limits.
2. **Per-app scan patterns.** An app can register an interface but cannot add its
   own injection patterns. `security.scan` stays free of Frappe imports, so this is
   a caller-side merge one level up — the same shape `_scan_context` uses for
   `history` — not a hooks lookup inside `scan()`.
3. **Webhook replay protection.** `api.trigger_automation` trusts Frappe token auth
   alone. An optional shared-secret HMAC over payload + timestamp, with a freshness
   window, closes the replay case for the one crema entry point an external system
   fires.

### Guarantee gaps (detail in docs/security.md — Planned hardening)

4. **A user-scoped response cache.** The cache key today has no user and no
   requested-interface name, so a reply cached for one user can serve another, and
   two interfaces that fall back to the same provider collide. Add the session user
   and the requested interface to the key, or record precisely why each omission is
   safe.
5. **Version stamping in audit rows.** Record `response.model` (the model actually
   served) and the crema app version in Crema Log, inside `CHAIN_FIELDS`, so an
   incident replays against the exact stack that produced it.
6. **SSRF egress control for `automation._fetch`.** A deny-list for private and
   loopback addresses, or an allow-listed egress proxy, closing the
   redirect-following gap.

### Experience and plumbing

7. **Buffer-then-release streaming.** The full reply arrives, the guardrail onion
   runs, and the desk renders progressively over `frappe.publish_realtime`. No
   guarantee weakens — the trap validates its nonce and masking swaps its tokens on
   the complete response. A wait of a few seconds is fine in an ERP form.
8. **Background execution for long completions.** An opt-in enqueue path — the
   worker shape automation already uses — with the result delivered over
   `frappe.publish_realtime`. Frees the web worker; pairs with item 7.
9. **Child tables in a Document Query.** `_source_fields` keeps to
   `data_fieldtypes`, so a record travels to the model without its line items —
   no item rows on a Sales Invoice, no components on a BOM. Serialise each child
   row through the same explicit-fieldlist fence, capped per record.
10. **Scheduled provider health checks.** `client.check_connection` is the existing
    half; a scheduled version of the same check, feeding provider `enabled`
    automatically, is the missing half of automatic fallback.
11. **Multi-stage record matching.** Stage `crema_widen_if_empty` (exact `=`, then
    `like`, then per-word) so the desk view path's term fallback lands more often —
    still inside the `crema_readable_fields` fence, still no second LLM call.
12. **A field-writing helper.** A button on long text fields that drafts content in
    place, over the existing `transform` instruction→diff path. A thin UI layer,
    and the kind of small bounded desk feature that fits "interface layer, not
    chatbot" — it edits one field the user is already allowed to edit.

### Included, but not next

- **Permission-fenced tool calling.** Let an interface call
  `frappe.get_list`/`frappe.get_doc` under its isolation user, reusing the sandbox
  fence `ask()` uses for files. Three constraints are part of the design: tools stay
  read-only (a read-only turn is the one structural defence against injection that
  does not depend on a detector); a gateway-side LLM guard judges the proposed tool
  call, not free text; every tool result is truncated before it enters the context.
  Big machinery — build it when a consumer exists.
- **Related-record context for a Document Query** (invoice against its order and
  receipt). Falls out of tool calling for free; do not build a join-configuration
  surface before it.
- **Per-source reply checks.** One nonce per context channel, so a sprung trap
  names the compromised channel. Cheap to arm; needs measuring against real models'
  echo reliability first.
- **Per-task prompt templates with typed arguments.** YAGNI until two tasks
  actually share a prompt.
- **Embeddings and document search.** Named here so the idea has a home; no defined
  need yet.

## Rejected

Each with the reason, so the argument does not repeat.

- **A chat surface.** Permanently out of scope: no session store, no turn
  management, no chat UI. `ask(..., history=...)` lets a caller hand crema its own
  transcript for one call; crema owns one system prompt per interface and persists
  no conversation. This is the line between "hardened interface layer" and the five
  chat assistants the ecosystem already has.
- **In-process NER / Presidio.** presidio-analyzer plus a spaCy model is hundreds
  of MB per bench worker, English-biased, and duplicates `mask._SENSITIVE`. The
  layers compose instead: crema masks record terms first (placeholders are not
  PII), the gateway's Presidio catches free-text remainders with its own restore.
  A site that wants NER without a proxy plugs a sidecar in through
  `crema_guardrails`.
- **An in-crema AI guard** (second-model text classifier). Removed. A check over
  bare text with zero Frappe context belongs on the gateway, run once per bench
  with per-key control. The `crema_guardrails` hook remains the escape hatch.
- **A per-provider budget.** Removed. A provider is an infrastructure identity;
  behind a proxy this is the proxy key budget, without one it is the vendor's own
  spend limit. Crema meters use cases and people: interface and user budgets stay.
- **Growing the Text Scan.** No separator squeezing, leet folding,
  decode-and-rescan, scored second layer, or per-language pattern tables. Each
  trades the scan's one guarantee — deterministic and cheap enough to run before
  the answer cache — for a fuzzier, costlier check. Semantic paraphrase is the
  gateway's job. The look-alike-letter gap stays a Known limit.
- **Demoting the scan to telemetry-only.** The scan is the cheap, deterministic,
  fail-closed pre-filter, not the detector of record. A site that wants
  telemetry-only sets the row to `Log Only`; the ladder exists per row.
- **RAG / knowledge indexing.** Pulls toward chatbot and adds a vector-store
  dependency.
- **Visual flow builders, multi-agent crews.** The pipeline is deliberately fixed
  (SOURCE → PLAN → EXTRACT → WRITE); a builder dissolves the validation surface
  `_validate_plan` depends on.
- **Becoming an MCP server.** Four ecosystem projects do this already, one
  first-party. If ever needed, expose `api.py` functions through frappe/mcp rather
  than building transport.
- **Live context injection** (a company snapshot in every prompt). Widens the data
  sent per call; crema's posture is minimum content per call.
- **True token-by-token streaming with checks on.** The shape would be a
  sliding-window scan with mid-stream kill and retract — real new machinery, not
  before someone needs it. Item 9's buffer-then-release covers the ERP case.

## Done (the record the absorbed notes carried)

- **Undo a run.** `_upsert`/`_upsert_files` take a `written` accumulator and record
  each saved record's (doctype, name) as created or updated, deduplicated so a
  replan retry that later updates its own earlier create still counts as created.
  `run_task` builds one accumulator per run and `_record` stores it as
  `last_written_json` on every path, including a Failed one and the
  interface-unresolvable early return, so the field never goes stale. `undo_last_run`
  deletes every created record through plain `frappe.delete_doc` (no new write path,
  its own permission and link-exists fences stay on) and leaves updated records for
  manual review. `apply_proposal` stamps the same list onto
  `Crema Proposal.created_json`; `undo_proposal` reverses an Approved row and returns
  it to `Pending`. Every crema-written record also carries `flags.updater_reference`
  to the task, the same provenance marker `data_import` uses, so it shows up in the
  record's own Document History panel.
  Considered and rejected: Frappe's `Version` doctype as the store (it needs the
  *target* doctype's own `track_changes`, and writes nothing on insert without this
  same `updater_reference` flag already set — so half of it is adopted as provenance,
  not as the store); a `Crema Run` history doctype (deferred — one run's list on the
  task field covers the "undid the wrong thing" case, and a history doctype is easy
  to add later without touching the writer code).
- **A `Propose Only` automation action, with a confidence floor.** A task runs the
  pipeline through EXTRACT and parks each row as a `Crema Proposal` — a new
  doctype — instead of writing it; **Approve** replays it through the same
  `_upsert`/`_upsert_files` a `Create or Update Records` run would have used,
  **Discard** removes it. A parked row's `fingerprint` (task + source + the
  payload's own JSON) is what makes a repeated run — a crash, a retry — find its
  earlier proposal instead of filing a duplicate. The confidence floor
  (`confidence_floor`) applies to a File Query source only: that is the one path
  with a measured (OCR-pass) confidence rather than a model self-assessment, and
  it gates every action, not only `Propose Only`.
- **The guardrails pipeline** — one ordered, site-wide doctype (`Crema
  Guardrails`), module registry, per-row action and use-case filter, and the
  `crema_guardrails` app hook. Replaced the fixed security layers and the old
  `security` interface.
- **Reversible masking** (experimental) — record-term harvest under the isolation
  user, pattern masking, capitalised-name sweep, health keyword list, exact
  restore. The term harvest is the part no gateway can do: exact values from the
  document and its links, permission-scoped.
- **The gateway split** — AI Guard removed, per-provider budget removed, scan scope
  frozen, proxy cost header preferred in `_record_usage`, caller identity and
  interface tag stamped on every call, gateway story documented.
- **Ops hardening** — kill switch, per-user monthly budget ceiling, blocked-doctype
  list, escalation notification on blocked calls, tamper-evident log chain
  (`chain_sha`), litellm floor+ceiling pin, `ocr` HTTP endpoint.
- **Test bootstrap for consuming apps** — `crema.testing.seed_provider`, documented
  in docs/use.md.
- **The v16→v17 port** — workspace sidebar folded into the workspace fixture,
  desktop icon fixed, badge/toast/indicator selectors updated, bulk-delete gate
  checked up front. (The `add_to_apps_screen` hook was considered and dropped;
  see `crema/hooks.py`.)
- **The ecosystem survey verdict** (2026-08-06, awesome-frappe, 15 projects read):
  nothing else is a hardened in-process LLM interface layer. The clusters are chat
  assistants, MCP bridges, agent platforms, a credential-governance layer
  (Pacioli — complementary), and dev tooling. Several chatbots independently grew
  crema-like defences; crema's bet is to ship only that part, as infrastructure.
