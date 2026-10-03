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
(`crema.testing`), a hook for a site's own guardrail (`crema_guardrails`), a hook
for an app's own scan patterns (`crema_scan_patterns`), an interface registry for
an app's own use cases (`crema_interfaces`), per-interface and per-user budgets,
and one audit row per call that never stores content. The identity to build on, and
the thing to be remembered for: **propose, never write** — every surface returns a
proposal that a human or the caller applies.

The gaps that ordered the list below: automation is the one surface that writes
unattended, and a slow completion holds a web worker for its whole duration.

## Included

Ordered by benefit to a programmer integrating crema.

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
- **Buffer-then-release streaming.** No desk surface renders a prose reply in the
  first place — `crema_ask` consumes a structured view spec, the transform dialog a
  diff, extract a record list. A server-side chunked publish loop would be machinery
  built for a consumer that does not exist; once a reply is delivered over
  `frappe.publish_realtime` (as `extract_async` does — see docs/security.md's
  Background execution), any future prose surface can reveal it progressively in its own JS with no crema
  change.
- **True token-by-token streaming with checks on.** The shape would be a
  sliding-window scan with mid-stream kill and retract — real new machinery for a
  consumer (a prose reply surface) that does not exist, same reasoning as
  buffer-then-release above.
- **Background execution for `ask`/`transform`.** Both are one provider call the
  user is already watching, at a provider's own `timeout_seconds` (default 60). An
  async path delivers the same answer at the same moment and costs a new endpoint, a
  worker entry, a realtime channel, a correlation id, and a second error shape, for
  no benefit to the person waiting. `extract` is different: it can make three provider
  calls, so it has `extract_async` — see docs/security.md's Background execution.
- **Frappe's `Version` doctype as the undo store.** It needs the *target* doctype's
  own `track_changes`, and writes nothing on insert unless `updater_reference` is
  already set. Half of it is adopted as provenance (`flags.updater_reference`), not as
  the store; a run's created and updated records live on the task's
  `last_written_json`.
- **A `Crema Run` history doctype.** Deferred: one run's list on the task covers the
  "undid the wrong thing" case, and a history doctype can come later without a change
  to the writer code.
- **The `add_to_apps_screen` hook.** Dropped in the v16→v17 port; see
  `crema/hooks.py`.

Shipped work is not listed here: `git log` and [docs/changes.md](docs/changes.md)
hold it.
