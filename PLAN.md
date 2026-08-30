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
  `frappe.publish_realtime` (see Background execution for `extract` in Done, below),
  any future prose surface can reveal it progressively in its own JS with no crema
  change.
- **True token-by-token streaming with checks on.** The shape would be a
  sliding-window scan with mid-stream kill and retract — real new machinery for a
  consumer (a prose reply surface) that does not exist, same reasoning as
  buffer-then-release above.
- **Background execution for `ask`/`transform`.** Both are one provider call the
  user is already watching, at a provider's own `timeout_seconds` (default 60). An
  async path delivers the same answer at the same moment and costs a new endpoint, a
  worker entry, a realtime channel, a correlation id, and a second error shape, for
  no benefit to the person waiting. `extract` is different — see Done, below, for the
  reasoning and the version that shipped instead.

## Done (the record the absorbed notes carried)

- **A per-field draft button.** A writable Text/Long Text/Small Text field on a saved
  document now carries its own small button next to its label — `ControlText.prototype.
  refresh`, the same hook and "already attached" guard core's own
  `show_translatable_button` uses for a translation button in the same spot, not
  `make_input()`: a field's writable/hidden status and a document's local/saved status
  can both change after the control is first built (a new document becomes saved;
  `depends_on` flips a field read-only), and `refresh()` is what re-evaluates them on
  every render. Reuses `transform`'s existing instruction→diff path, scoped to one
  field: the client keeps only `data.set[fieldname]` from the reply, dropping anything
  else the model proposed, the same discipline `_filter_diff` already applies
  server-side to an invented fieldname. `Text Editor` is out of scope (its own toolbar,
  a different control class). No server change — `transform_api` needed nothing new.
- **Background execution for `extract`.** `extract` can hold a web worker for minutes on
  a scanned file (OCR, its `advanced_ocr` escalation, then the extraction call, each
  retried once) — long enough to starve every other user on a small bench, not only the
  one who asked. Added `api.extract_async` — the same gates as `extract_api` (role, both
  rate limits), applied in the request that queues the job, not the worker:
  `_check_user_rate_limit` is a no-op without `frappe.local.request`, so checking it in
  the worker instead would let a queued call skip the per-user budget entirely. The
  worker, `api.run_extract`, is deliberately unwhitelisted (`frappe.enqueue` resolves
  the dotted path itself — a whitelisted twin would be a second, ungated entry point
  into `extract()`) and publishes its result over `frappe.realtime`, always scoped to
  the enqueuing user (`user=frappe.session.user`, the identity `frappe.enqueue`'s own
  `execute_job` set before the job ran) — never a bare, room-less publish: a result
  carries document content, and `publish_realtime`'s own default with no room is the
  whole site. The desk's own file-upload path (`crema_extract_into_new_doc`) now calls
  `extract_async` and closes its dialog instead of freezing the page; a
  `frappe.realtime.on("crema_extract", ...)` listener reopens the same dialog object
  when the result lands — `dialog.hide()` only toggles Bootstrap's own `.show` class, so
  the dialog and its fields survive to be re-populated. That listener is registered
  lazily, on the first extract, not as a bare top-level `frappe.realtime.on` call at
  bundle load: `RealTimeClient.on()` (frappe core's `socketio_client.js`) is a silent
  no-op until `frappe.realtime.init()` has run, which happens in `Application.startup()`
  on `$(document).ready` — after every `app_include_js` bundle's own top-level code,
  this one included, has already been evaluated as a `<script>` tag. Caught by an actual
  cypress run against a real socket, not by the Python suite, which mocks at
  `client._complete` and never exercises the browser's own listener. `ask`/`transform`
  stay foreground, and there is no server-side progressive streaming — see Rejected.
- **Child tables in a Document Query.** `_source_fields` kept to `data_fieldtypes`,
  which excludes `Table`/`Table MultiSelect` — a Sales Invoice reached the model with
  no item lines, a BOM with no components. Added an opt-in **Lines** box
  (`read_children`) on a Document Query source: `automation._attach_children` merges
  each `Table`/`Table MultiSelect` field's rows onto the matching parent row dict,
  under the table's own fieldname, through `_child_fields` — `_source_fields` minus
  `name`/`modified` — so the read fence (`data_fieldtypes`, no `Password`, no
  permlevel) is defined in exactly one place. One `frappe.get_list` per child table
  for the whole batch, `parent_doctype=doctype` so frappe's own child-table
  permission check stays the fence, never `get_all`. `_CHILD_MAX_ROWS_PER_RECORD`
  (20) is a budget per record shared across every table on the doctype, in field
  order, enforced in Python after the read since the query's own `limit` only bounds
  the whole batch. Called in `_read_documents` **before** the per-row security-scan
  triage, not after: a child row's text is a better injection surface than the
  parent's own fields, and a poisoned line item must drop the whole record, not sail
  through unscanned. `CremaAutomationSource.validate` zeroes it on the URL and File
  Query branches, the same value-based honesty the grid's other per-row columns
  already need.
- **Scheduled provider health checks.** `client.check_connection` was the existing
  half — a live, uncached probe a human triggers by opening the Providers panel —
  with nothing running it on a schedule, so a provider that stopped answering stayed
  `enabled` and `client._resolve` kept picking it. Added `client.check_providers`
  (hourly scheduler entry), gated on a new Crema Settings box, **Switch Off Services
  That Do Not Answer** (`auto_disable_unreachable`), off by default: a gateway that
  serves completions but does not implement `/models` would otherwise be switched
  off every sweep — an outage caused by the health check itself. Per provider, each
  in its own try/except: stamps `last_checked`/`last_check_detail` (new Crema
  Provider fields) via `frappe.db.set_value`, flips an unreachable `enabled` provider
  off (`auto_disabled=1`) or a recovered `auto_disabled` one back on, and calls
  `cache.clear_provider()` once if anything flipped — load-bearing, since
  `_resolve_one`'s redis cache is keyed per interface and a flip that skipped it
  would leave a resolved config pointing at the provider that just changed.
  `CremaProvider.validate` unconditionally clears `auto_disabled`, so a human save
  always re-asserts intent: the sweep writes through `frappe.db.set_value`, which
  does not run `validate`, so only a provider crema itself switched off is ever
  switched back on. No flap counter — a transient timeout disables a provider for
  one hour and the next sweep restores it, with fallback covering the gap; the
  ceiling carries a `ponytail:` comment naming the counter as the upgrade path.
- **Multi-stage record matching — already shipped.** `crema_widen_if_empty`
  (`crema.bundle.js`) already stages a whole-term `like` probe, a free `=` upgrade
  when a row's winning field equals the term, then a per-word probe, with Cypress
  coverage at `cypress/integration/desk_ui.js`. Landed in `aa23b12` (2026-08-07);
  this entry retires the corresponding Included item, which had drifted out of sync
  with the code.
- **SSRF egress control for `automation._fetch`.** `_fetch` was a bare `requests.get`
  with redirects followed and no host check. Added `automation._check_egress`: refuses
  a non-http(s) scheme, resolves the host with `socket.getaddrinfo`, and refuses any
  address `ipaddress.ip_address(...).is_global` calls non-global (private, loopback,
  link-local — so a cloud metadata endpoint at `169.254.169.254` is refused the same as
  `127.0.0.1`). `_fetch` now follows redirects itself, one hop at a time
  (`allow_redirects=False`), running the check before every hop rather than only the
  first — a public start URL that 302s to a private address is caught on the second
  hop. A host that fails to resolve at all is let through deliberately: there is
  nothing yet to classify, and `requests` fails on it a moment later — this is also
  what keeps the test suite's unresolvable `*.invalid` fixtures (RFC 6761) working with
  no DNS mock. The residual gap, recorded in `docs/security.md`'s Known limits: the
  check resolves the host once and `requests` resolves it again a moment later, so a
  DNS answer that changes between the two (rebinding) is not caught.
- **Version stamping in audit rows.** A Crema Log row recorded only the *configured*
  model name, never the model a provider's response actually reported, and no crema
  app version — an incident could not be replayed against the exact stack that
  produced it. `client._record_usage` now reads `response.model` (guarded by
  `isinstance`, the same MagicMock-auto-vivification trap `_as_number`/`_proxy_cost`
  already guard against) into the request-local usage accumulator as `served_model`;
  last write wins on purpose, so a fallback chain or an OCR escalation billing two
  provider calls under one row shows the model behind the answer actually returned.
  `log.insert` stamps that alongside `app_version` (`crema.__version__`) on every row.
  Both joined `CHAIN_FIELDS`, so a migration was required: `crema.patches.
  rechain_crema_log_for_version_fields` re-verifies every existing row's chain under
  the frozen pre-change field tuple first, and only re-chains under the new tuple if
  that verification is clean end to end — a tamper that predates the upgrade stays
  visible instead of being silently re-hashed away. Existing rows are not backfilled;
  `served_model`/`app_version` stay blank on them.
- **A user-scoped response cache — resolved without adding the user.** The cache
  key (`api._prompt_hash`, also the audit log's `prompt_sha`) gained the
  *requested* interface name and the isolation user: `guardrails._plan` filters
  guardrail rows by `cfg["requested"]`, not the fallback-resolved `cfg["interface"]`
  the old key carried, so two use cases falling back to the same provider and
  sharing a system prompt could collide into one entry and serve a permissive
  reply to a strict interface. The session user stays out on purpose — no part of
  a crema request is assembled under the calling user's permissions, every read
  happens inside `sandbox.isolation(cfg["isolation_user"])`, so an identical
  request implies identical readable inputs whoever asks. That argument is now
  written down (`docs/security.md` — "The answer cache") and pinned by
  `test_a_cached_reply_is_shared_between_two_users`, rather than assumed. The hash
  also switched from a `"|"` join to `json.dumps` of an ordered list, closing a
  free ambiguity: `prompt="a|b"` and `prompt="a", context="b"` used to hash
  identically.
- **Webhook replay protection.** `api.trigger_automation` trusted Frappe token auth
  alone — a captured request could be re-sent unchanged and would start the task
  again. Added an optional `webhook_secret` (Password) on Crema Automation Task;
  while set, `trigger_automation` also requires `timestamp` and `signature` — hex
  HMAC-SHA256 of `{timestamp}.{task}.{payload}`, keyed by the secret, checked with
  `hmac.compare_digest`. A timestamp more than 5 minutes off is refused, and a seen
  signature is spent — `frappe.cache` marks it for twice the freshness window, so
  the same signed call cannot both pass the window check and be replayed inside it.
  Verified against the payload as received, before automation's own truncation. A
  task with no secret set is unchanged: Frappe token auth alone, as before.
- **Per-app scan patterns.** An app could register an interface (`crema_interfaces`)
  or a whole guardrail (`crema_guardrails`), but had no way to add its own
  injection patterns to the Text Scan — a `pre_cache` guardrail module would have
  had to reimplement `security._canon`'s four-step canonicalisation itself, or lose
  to a fullwidth or Cyrillic spelling. Shipped `crema_scan_patterns` — a
  `{reason: pattern_string}` dict in an app's `hooks.py`, read by the new
  `guardrails.scan_patterns()` (first app wins a duplicate reason, an uncompilable
  pattern is dropped with a warning), and passed into `security.scan`'s new `extra`
  argument. `security.py` stays free of any Frappe import: the merge happens in
  `_Scan.before` and in `automation._read_documents`'s per-row triage — the scan's
  other caller — not inside `scan()` itself. An app pattern is appended LAST, so a
  built-in reason still wins when both match, and it is compiled with the same
  `re.I | re.S` and seen through the same canonicalisation as every built-in one.
- **The Reply Filter guardrail.** The output-side hook the old item 1 asked for
  already existed — `guardrails.run`'s `after(ctx)` half, documented in
  `docs/configure.md`'s "Add your own guardrail" — it just had no built-in use and a
  stale security-page line ("Crema has no equivalent hook") nobody had gone back to
  fix. Shipped the use the security page's Known limits already promised: a built-in
  `reply` guardrail (`Off` by default) that rewrites remote markdown image/link
  targets and HTML `src`/`href` to `#`, closing the zero-click exfiltration channel
  a rendered reply can open. Seeded second in `_BUILTINS` (right after the Text
  Scan), so its `after()` — reverse row order — runs LAST and sees the fully
  mask-restored, nonce-stripped reply. `client._transcribe` still bypasses the
  onion entirely and is recorded as a Known limit, not fixed here — new machinery
  for a path with no system prompt.
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
