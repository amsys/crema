"""Public API — the only entry surface into crema.

No public function anywhere accepts a model/provider/api_key parameter; that
parameter only exists inside client._complete. Direct provider calls are
structurally impossible from caller code.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import json
import mimetypes
import time
from typing import Any

import frappe
from crema import _ocr as _ocr_impl
from crema import cache, client, guardrails, interfaces, sandbox
from crema import log as _log_mod
from crema import terms as _terms
from crema._json import strip_fence
from crema.exceptions import CremaBlockedError, CremaBudgetError, CremaConfigError
from frappe import _
from frappe.rate_limiter import rate_limit

# Exactly the programmer surface crema/__init__.py re-exports, plus the three exceptions.
# The whitelisted endpoints below are deliberately absent: they are addressed by dotted
# path over HTTP, so naming them here buys them nothing.
__all__ = [
    "CremaBlockedError",
    "CremaBudgetError",
    "CremaConfigError",
    "ask",
    "ask_json",
    "configure",
    "extract",
    "health",
    "is_configured",
    "ocr",
    "transcribe",
    "transform",
]

_USER_RL_LIMIT = 60
_USER_RL_SECONDS = 3600

# trigger_automation's optional HMAC: how far a timestamp may drift either side of now,
# and how long an accepted signature is remembered as spent (twice the window, so a
# signature can never be replayed once it falls out of the freshness check either).
_WEBHOOK_WINDOW_SECONDS = 300
_WEBHOOK_NONCE_TTL_SECONDS = _WEBHOOK_WINDOW_SECONDS * 2

_EXTRACT_RULES = """Read the document below and fill in the fields of the target doctype.
Use ONLY the fieldnames listed — never invent one. Omit any field the document does not
state; do not guess a value. Decide for yourself how many separate documents this file
describes: one document about one thing is ONE record, even if it has many line items
(those are child_set rows, not separate records). A file listing several separate things
(a statement of many invoices, a roster of many people) is one record per thing. Never
invent a record the document does not state. Output ONLY JSON with exactly this shape:
{"records": [{"set": {"<fieldname>": <value>},
              "child_set": {"<table fieldname>": [{"<child fieldname>": <value>}, ...]}}, ...],
 "reason": "<one sentence: what this document is, how many records you found, and what you
could not fill in>"}"""


def _prompt_hash(
    cfg: dict[str, Any],
    prompt: str,
    context: str | None,
    response_format: dict | None,
    history: list[dict] | None = None,
) -> str:
    """One value, deliberately: this is both the response-cache key
    (cache.response_key) and the audit log's prompt_sha. Splitting them would let
    prompt_sha stay blind to a discriminator that changes the reply, which is worse
    for an audit trail than a fingerprint whose recipe grew by two fields.

    cfg["requested"] is in it because guardrails._plan filters rows by the interface
    the caller asked for, not the one the fallback walk resolved to (cfg["interface"])
    — without it, two requested interfaces that fall back to the same provider and
    share a system prompt would share one cache entry, and a reply produced under a
    permissive guardrail posture could be served to a strict one.

    cfg["isolation_user"] is in it because it is the sandbox every context read runs
    under (see docs/security.md — The answer cache) — repointing an interface at a
    different isolation user must not let an old, differently-scoped reply survive
    the change.

    No session user is in it, on purpose: nothing in a request is assembled under the
    *calling* user's permissions, so an identical request implies identical readable
    inputs whoever asks — see docs/security.md — The answer cache for the argument in
    full.

    A JSON list, not a "|" join: a join is ambiguous between prompt="a|b" and
    prompt="a", context="b" — two different resolved prompts that must not share a
    cache entry.
    """
    parts = [
        cfg.get("requested") or cfg["interface"],
        cfg["interface"],
        cfg.get("isolation_user") or "",
        cfg.get("model") or "",
        cfg.get("system_prompt") or "",
        prompt,
        context or "",
        json.dumps(response_format, sort_keys=True) if response_format else "",
        json.dumps(history) if history else "",
    ]
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def _user_content(prompt: str, context: str | None) -> str:
    """The exact string the model receives as the user turn.

    The input gate scans THIS join, not prompt and context separately: the model sees
    them concatenated, so an injection split across the two fields ("...please ignore
    all" in context, "previous instructions..." in prompt) is only visible here.
    Scanning the concatenation is strictly stronger than scanning each field, since
    each field remains a substring of it.
    """
    return f"<context>\n{context}\n</context>\n{prompt}" if context else prompt


def _build_messages(
    cfg: dict[str, Any], prompt: str, context: str | None, history: list[dict] | None = None
) -> list[dict]:
    return [
        {"role": "system", "content": cfg.get("system_prompt") or ""},
        *(history or []),
        {"role": "user", "content": _user_content(prompt, context)},
    ]


def _scan_context(context: str | None, history: list[dict] | None) -> str | None:
    """Widen the text layer 1 scans to include a caller-supplied history — scan()
    joins whatever it's given (see its own docstring on why), so an injection split
    across an earlier turn is still caught, not just the latest one."""
    if not history:
        return context
    turns = "\n".join(str(m.get("content", "")) for m in history)
    return f"{context}\n{turns}" if context else turns


def _gate_or_block(
    cfg: dict[str, Any], prompt: str, context: str | None, history: list[dict] | None, prompt_sha: str
) -> None:
    """Anchor A of the guardrails pipeline (crema.guardrails.gate) — every pre-cache
    guardrail (the text scan) runs here, over context+history+prompt joined the same
    way the model will see them concatenated, BEFORE the answer cache and the budget
    check. A hit is logged Blocked and raised."""
    scoped = _scan_context(context, history)
    text = f"{scoped}\n{prompt}" if scoped else prompt
    try:
        guardrails.gate(cfg, text)
    except CremaBlockedError as exc:
        _log(cfg, "Blocked", str(exc), prompt_sha=prompt_sha)
        raise


def _resolve_files(files: list[str | tuple[bytes, str]]) -> list[dict]:
    """Permission-checked (as the calling user) content parts for vision models.

    A `str` is a Frappe File URL — permission-checked via _ocr_impl._load_bytes, the
    same fence ocr()/extract()/transcribe() apply, before its content is read. A
    `(bytes, mime)` tuple is content the caller already read under its own permission
    check — e.g. a bare image with no Frappe File behind it at all (a bot photo) — and
    is prepped as-is, no re-check possible or needed.

    Delegates to crema._ocr.prep_parts, which routes PDFs through the same text-vs-scan
    detection ocr() uses (extracted text for text PDFs, rendered page images for
    scanned ones) and downscales plain images. Anything else is dropped.
    """
    parts: list[dict] = []
    for f in files:
        if isinstance(f, tuple):
            content, mime = f
            parts.extend(_ocr_impl.prep_parts(content, mime))
            continue

        try:
            content, mime = _ocr_impl._load_bytes(f)
        except Exception:
            continue

        parts.extend(_ocr_impl.prep_parts(content, mime))
    return parts


def _attach_files(cfg: dict[str, Any], messages: list[dict], files: list | None) -> list[dict]:
    """Rewrite the last (user) turn to carry `files` as vision-model content parts, if
    any given. Must run inside `sandbox.isolation(...)` — _resolve_files permission-
    checks each File as the isolation user — and its own layer-1 scan is what stops
    ask(files=[...]) from bypassing what extract() blocks: a text PDF's extracted text
    only exists here, after layer 1 already ran on prompt/context. Image parts hold no
    text to scan. Returns `messages` unchanged when there is nothing to attach."""
    file_parts = _resolve_files(files) if files else []
    if not file_parts:
        return messages

    # Gate the extracted text so the same bytes block on this path as on extract()'s,
    # which hands the read text to the gate via context=. Raises CremaBlockedError,
    # logged by _Ask.__call__'s own handler.
    text = "\n".join(p["text"] for p in file_parts if p["type"] == "text")
    if text:
        guardrails.gate(cfg, text)

    last = {"role": "user", "content": [{"type": "text", "text": messages[-1]["content"]}, *file_parts]}
    return [*messages[:-1], last]


def _log(
    cfg: dict[str, Any],
    status: str,
    detail: str | None,
    prompt_sha: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """Insert a Crema Log row. Never stores prompt content — only correlation data.

    Thin wrapper over crema.log.insert (the shared helper crema._ocr also uses) that
    unpacks the resolved-config dict this module works with everywhere else.
    """
    _log_mod.insert(
        cfg.get("interface"),
        cfg.get("model"),
        status,
        detail,
        prompt_sha=prompt_sha,
        duration_ms=duration_ms,
        provider=cfg.get("provider"),
    )


class _Ask:
    """Callable `ask` singleton — also supports attribute-style sugar.

    `ask("simple", "text")` and `ask.simple("text")` are equivalent; the attribute
    form is just `functools.partial(self, interface)` via `__getattr__`. Both go
    through the exact same `__call__` flow below (the input gate, cache, budget, and
    the sandboxed, guardrail-wrapped completion) — the sugar changes nothing about
    what runs. A single
    positional argument, `ask("text")`, is a third equivalent form: it runs the
    `simple` interface, same as `ask("simple", "text")`.
    """

    def __call__(
        self,
        interface: str,
        prompt: str | None = None,
        *,
        context: str | None = None,
        files: list[str | tuple[bytes, str]] | None = None,
        response_format: dict | None = None,
        cache_ttl: int | None = None,
        history: list[dict] | None = None,
    ) -> str:
        """Ask a configured interface. See the app plan for the full ordered flow.

        Called with one positional argument, that argument is the prompt and the
        interface defaults to `simple` — `interfaces.LABELS` calls it "Default" and
        it's the end of every fallback chain.

        `cache_ttl`, if not None, overrides the interface's configured cache_ttl for
        this call only (e.g. to force-cache a call whose interface has caching off).
        No model/provider/api_key override exists here or anywhere public — that
        boundary is intentional, see the module docstring.

        `history`, if given, is a list of prior `{"role": "user"|"assistant",
        "content": str}` turns inserted between the system prompt and this call's user
        turn. The interface's system prompt is still the only one, and crema stores
        and manages no conversation of its own — the caller owns the transcript and
        passes it back each turn. Scanned for injections together with `context` and
        `prompt`, so a turn that hides an override doesn't get a pass for not being
        the latest one.
        """
        if prompt is None:
            interface, prompt = "simple", interface

        cfg = client._resolve(interface)
        if cache_ttl is not None:
            cfg = {**cfg, "cache_ttl": cache_ttl}

        prompt_sha = _prompt_hash(cfg, prompt, context, response_format, history)

        _gate_or_block(cfg, prompt, context, history, prompt_sha)

        # A call carrying files is never cached, read or write: _prompt_hash cannot see
        # the file bytes (it doubles as the audit log's prompt_sha, and a File URL's
        # content can change under an unchanged URL), so two calls with the same prompt
        # and different files would otherwise collide on one cache entry.
        cacheable = bool(cfg["cache_ttl"]) and not files

        if cacheable:
            cached = frappe.cache.get_value(cache.response_key(prompt_sha))
            if cached is not None:
                # Served with no provider call — logged as its own status, not
                # "Success", so hit rate and "served vs. billed" are computable from
                # Crema Log alone.
                _log(cfg, "Cached", None, prompt_sha=prompt_sha)
                return cached

        try:
            _log_mod.check_budget(cfg)
        except CremaBudgetError as exc:
            _log(cfg, "Blocked", str(exc), prompt_sha=prompt_sha)
            raise

        # Masking and the reply check both run inside client._complete's guardrails
        # onion (crema.guardrails.run) — after the cache and the budget check, so a
        # cached or budget-stopped call never bills a check that costs money.
        messages = _build_messages(cfg, prompt, context, history)

        start = time.monotonic()
        try:
            with sandbox.isolation(cfg["isolation_user"]):
                messages = _attach_files(cfg, messages, files)
                result = client._complete(cfg, messages, response_format)
        except CremaBlockedError as exc:
            # Raisers: any guardrail inside client._complete's onion (a reply-check
            # miss, an unrestorable mask token), and the gate over a file's extracted
            # text just above. Logged as Blocked, not
            # Error, so a block is distinguishable from a provider failure in the
            # audit log — that distinction is what makes each guardrail's
            # false-positive rate measurable.
            _log(
                cfg,
                "Blocked",
                str(exc),
                prompt_sha=prompt_sha,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
            raise
        except Exception as exc:
            # A provider timeout / HTTP error / bad model name must still leave an audit
            # row — this is the primary audited surface, and losing the trail exactly
            # when things break is the worst time to lose it.
            _log(
                cfg,
                "Error",
                _log_mod.redact(f"{type(exc).__name__}: {exc}")[:2000],
                prompt_sha=prompt_sha,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
            raise
        duration_ms = int((time.monotonic() - start) * 1000)

        if cacheable:
            frappe.cache.set_value(cache.response_key(prompt_sha), result, expires_in_sec=cfg["cache_ttl"])

        _log(cfg, "Success", None, prompt_sha=prompt_sha, duration_ms=duration_ms)
        return result

    def __getattr__(self, interface: str):
        if interface.startswith("_"):
            raise AttributeError(interface)
        return functools.partial(self, interface)


ask = _Ask()


def ask_json(interface: str, prompt: str, **kw: Any) -> dict:
    """ask() + fence-stripping json.loads."""
    kw.setdefault("response_format", {"type": "json_object"})
    raw = ask(interface, prompt, **kw)
    return json.loads(strip_fence(raw))


def _field_lines(meta) -> list[str]:
    return [
        f"- {df.fieldname} ({df.fieldtype}) {df.label or ''}{' [required]' if df.reqd else ''}".rstrip()
        for df in meta.fields
        if df.fieldname
    ]


def _meta_summary(doctype: str) -> str:
    """Fieldname/fieldtype/label/reqd listing for `doctype` and its child tables — rendered
    into a prompt so the model knows exactly which fields it may fill in. Shared by
    automation._planning_prompt and extract() below."""
    meta = frappe.get_meta(doctype)
    blocks = [f"Fields of doctype '{doctype}':", *_field_lines(meta)]
    for df in meta.fields:
        if df.fieldtype == "Table" and df.options:
            blocks.append(f"Fields of child table '{df.fieldname}' (doctype '{df.options}'):")
            blocks.extend(_field_lines(frappe.get_meta(df.options)))
    return "\n".join(blocks)


def _filter_diff(doctype: str, raw: dict) -> dict:
    """Drop any fieldname the LLM invented that isn't actually on the doctype (or,
    for child rows, the child doctype). Returns {"set", "child_set"} only — `raw` may
    be one record out of extract()'s "records" list, so the document-level "reason" is
    the caller's concern, not this per-record sanitizer's."""
    meta = frappe.get_meta(doctype)
    valid_fieldnames = {df.fieldname for df in meta.fields}

    set_ = {k: v for k, v in (raw.get("set") or {}).items() if k in valid_fieldnames}

    child_set: dict[str, list[dict]] = {}
    for table_fieldname, rows in (raw.get("child_set") or {}).items():
        child_df = meta.get_field(table_fieldname)
        if not child_df or child_df.fieldtype != "Table" or not isinstance(rows, list):
            continue
        child_fieldnames = {df.fieldname for df in frappe.get_meta(child_df.options).fields}
        child_set[table_fieldname] = [
            {k: v for k, v in row.items() if k in child_fieldnames} for row in rows if isinstance(row, dict)
        ]

    return {"set": set_, "child_set": child_set}


def transform(doctype: str, name: str, instruction: str, interface: str = "transform") -> dict:
    """Propose a diff for a document — never writes; the caller applies it."""
    cfg = client._resolve(interface)

    with sandbox.isolation(cfg["isolation_user"]):
        doc = frappe.get_doc(doctype, name)
        doc.check_permission("read")
        doc.apply_fieldlevel_read_permissions()  # same path frappe.client.get uses
        payload = doc.as_dict(no_default_fields=True)
        # Harvested inside isolation, like the read above: a term the isolation user
        # can't see must not enter masking's term list either — see
        # crema.terms.harvest's own docstring. Skipped entirely when no Hide
        # Personal Information row applies to this interface (guardrails.run drains
        # the accumulator unconditionally either way, so nothing can leak).
        if guardrails.active("pi", interface) != "Off":
            frappe.local.crema_terms = _terms.harvest(doctype, name)

    raw = ask_json(interface, instruction, context=frappe.as_json(payload))
    return {**_filter_diff(doctype, raw), "reason": raw.get("reason", "")}


def extract(doctype: str, file: str | bytes, instruction: str | None = None) -> dict:
    """Propose field values for one or more NEW `doctype` documents read out of `file` —
    never writes. Returns {"records": [{"set", "child_set"}, ...], "reason", "confidence"}
    — how many records a file holds (one, or several) is the model's call, not the
    caller's; a file describing a single thing always yields a one-element list.

    Two stages, both already built: ocr() turns the file into text (text-PDF extraction,
    vision OCR for scans, advanced_ocr escalation, audit row), then the `extraction`
    interface maps that text onto the doctype's fields. The confidence is the OCR pass's,
    so a caller can gate on it before trusting any record.

    Two isolation users are involved, deliberately: the file is read as the `ocr`
    interface's (inside ocr()), the doctype is fenced by the `extraction` interface's —
    each interface's own fence, as everywhere else in crema.
    """
    cfg = client._resolve("extraction")

    # No document exists yet, so "may this fence create one?" is what replaces
    # transform's doc.check_permission("read") — and it runs before any LLM spend.
    with sandbox.isolation(cfg["isolation_user"]):
        frappe.has_permission(doctype, "create", throw=True)
        schema = _meta_summary(doctype)

    # ponytail: a text PDF pays two LLM calls here (ocr() round-trips text pymupdf
    # already extracted). Fix belongs in _ocr.ocr() for all callers, not here.
    page = ocr(file, instruction=instruction)
    if not page["text"].strip():
        # Nothing was read. Handing the model a schema and an empty document is an
        # invitation to invent one, so propose nothing instead.
        return {
            "records": [],
            "reason": "no text could be read from the document",
            "confidence": page["confidence"],
        }

    parts = [_EXTRACT_RULES, schema]
    if instruction:
        parts.append(f"Caller instruction:\n{instruction}")
    # context=, like transform: the untrusted document text goes inside <context>...
    # </context> (see _user_content) rather than concatenated into the prompt, and the
    # input gate still runs on it, same as automation._extract's fetched content.
    raw = ask_json("extraction", "\n\n".join(parts), context=page["text"])
    records = [_filter_diff(doctype, r) for r in raw.get("records") or [] if isinstance(r, dict)]
    return {"records": records, "reason": raw.get("reason", ""), "confidence": page["confidence"]}


def ocr(file: str | bytes, instruction: str | None = None) -> dict:
    """OCR a frappe File URL or raw bytes. See crema._ocr for the full pipeline (PDF
    text-vs-scan detection, vision fallback, advanced_ocr confidence escalation).

    The implementation module is named `_ocr`, not `ocr`, so it cannot collide with
    this function's name once crema/__init__.py binds `ocr` onto the `crema` package.
    """
    return _ocr_impl.ocr(file, instruction=instruction)


def transcribe(file: str | bytes, *, language: str | None = None) -> dict[str, Any]:
    """Transcribe an audio file — a Frappe File URL or raw bytes, the same
    File-URL-or-bytes convention as ocr()/ask(files=[...]). Returns {"text": str,
    "language": str | None, "duration": float | None}.

    No system prompt exists for this interface (see interfaces.PREDEFINED/
    DEFAULT_PROMPTS), so the text scan doesn't run — same reasoning crema._ocr's
    module docstring gives for OCR'd document content: the scan checks text a
    caller supplies, and there is none here before the provider call happens (see
    client._transcribe's Hide-row refusal). Every call is still logged to Crema Log and
    budget-checked, same as ask()/ocr(); reuses _ocr_impl._load_bytes for the
    File URL / raw-bytes read rather than duplicating it — which means a File URL is
    permission-checked as the calling (session) user, same as ocr() (see
    docs/security.md).
    """
    cfg = client._resolve("transcribe")

    prompt_sha = None
    start = time.monotonic()
    try:
        content, mime = _ocr_impl._load_bytes(file)
        prompt_sha = hashlib.sha256(content).hexdigest()
        _log_mod.check_budget(cfg)
        # Whisper-style endpoints commonly derive the audio format from the multipart
        # filename's extension, not (only) the content type — an extension-less name
        # can be rejected outright.
        filename = "audio" + (mimetypes.guess_extension(mime or "") or "")
        result = client._transcribe(
            cfg, content, filename, mime or "application/octet-stream", language=language
        )
    except CremaBudgetError as exc:
        _log(
            cfg,
            "Blocked",
            str(exc),
            prompt_sha=prompt_sha,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
        raise
    except Exception as exc:
        _log(
            cfg,
            "Error",
            _log_mod.redact(f"{type(exc).__name__}: {exc}")[:2000],
            prompt_sha=prompt_sha,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
        raise

    _log(cfg, "Success", None, prompt_sha=prompt_sha, duration_ms=int((time.monotonic() - start) * 1000))
    return result


def health(interface: str = "simple", *, live: bool = True) -> dict[str, Any]:
    """Health snapshot for `interface`'s resolved configuration: provider, model,
    live reachability, and month-to-date spend/budget. Never returns or logs an
    api_key.

    Deliberately NOT @frappe.whitelist()'d and applies no role check of its own —
    check_provider (the Crema Settings equivalent) is only_for("System Manager"),
    too narrow for a consuming app's own dashboard/health-check button below that
    role. A caller applies whatever role gate its own surface needs (e.g.
    frappe.only_for(("Fleet Manager", "System Manager"))), then calls this
    in-process.

    {"configured": bool, "ok": bool | None, "reachable": bool | None,
     "provider": str | None, "model": str | None, "detail": str, "spend": float,
     "budget": float}. `ok`/`reachable` are None when `live=False`, or when nothing
     is configured (no network call to skip or make). `spend`/`budget` are the
     *resolved* interface's — if `interface` fell back to another configured one,
     that's the one actually billed (see client._resolve / log.check_budget), and
     the one these numbers describe.
    """
    try:
        cfg = client._resolve(interface)
    except CremaConfigError as exc:
        return {
            "configured": False,
            "ok": None,
            "reachable": None,
            "provider": None,
            "model": None,
            "detail": str(exc),
            "spend": 0.0,
            "budget": 0.0,
        }

    result: dict[str, Any] = {
        "configured": True,
        "ok": None,
        "reachable": None,
        "provider": cfg["provider"],
        "model": cfg["model"],
        "detail": "",
        "spend": _log_mod.month_spend()["interface"].get(cfg["interface"], 0.0),
        "budget": cfg.get("monthly_budget_usd") or 0.0,
    }
    if live:
        conn = client.check_connection(cfg["provider"])
        result.update(ok=conn["ok"], reachable=conn["reachable"], detail=conn["detail"])
    return result


def configure(
    interface: str,
    *,
    model: str | None = None,
    provider: str | None = None,
    monthly_budget_usd: float | None = None,
) -> dict[str, Any]:
    """Set an interface's model/provider/monthly_budget_usd on its Crema Model
    Assignment row, in-process. The supported alternative to a migration hand-rolling
    `settings.save()` for its reconcile side effect, then walking `settings.assignments`
    by fieldname (see PLAN.md item 9).

    Raises CremaConfigError if `interface` isn't in interfaces.names(). A row missing
    entirely (a brand-new PREDEFINED or app-registered name never saved before) is
    created first, via the same reconcile every Crema Settings save already runs.
    Every kwarg left at its default (None) is left untouched on the row — this is not a
    replace, only the named fields are written. Idempotent: calling it again with the
    same arguments changes nothing.

    Only these three fields: everything else on a Crema Model Assignment row (the
    system prompt, the isolation user, ...) — and the whole Crema Guardrails list —
    is intentionally left a desk edit, not a second code-side surface over the same
    settings.

    Deliberately NOT @frappe.whitelist()'d, like health() — an admin/migration
    affordance, and Crema Settings is already System-Manager-gated in the desk. Setting
    `provider` with no isolation user anywhere (row or Crema Settings' Default
    Isolation User) still raises from CremaSettings.validate — that fence is
    unchanged, and this function does not attempt to set an isolation user.

    Returns health(interface, live=False) — the resolved config after the write, or
    {"configured": False, ...} if the interface still doesn't resolve to a usable
    provider (e.g. model set before any provider exists)."""
    if interface not in interfaces.names():
        raise CremaConfigError(f"'{interface}' is not a known interface name.")

    settings = frappe.get_single("Crema Settings")
    if not any(row.interface == interface for row in settings.assignments):
        settings.save()
        settings = frappe.get_single("Crema Settings")

    row = next(row for row in settings.assignments if row.interface == interface)
    if model is not None:
        row.model = model
    if provider is not None:
        row.provider = provider
    if monthly_budget_usd is not None:
        row.monthly_budget_usd = monthly_budget_usd
    settings.save()

    return health(interface, live=False)


def is_configured(interface: str = "simple") -> bool:
    """Cheap, no-network check: does `interface` resolve to a usable (configured,
    enabled-provider) config at all? health(interface, live=False)["configured"]."""
    return health(interface, live=False)["configured"]


def _check_user_rate_limit() -> None:
    """The per-session-user half of ask_api's and extract_api's rate limiting.

    The @rate_limit decorator is IP-based: its `key` parameter reads
    `frappe.form_dict`, which is client-supplied, so it cannot express "per session
    user". This adds that, as a redis counter on a fixed hourly window, shared across
    both HTTP endpoints — one hourly budget per user for all crema HTTP calls. Like the
    decorator, it only applies to real HTTP requests — in-process callers (bench
    console, background jobs) are trusted code, not the surface this guards.
    """
    if not getattr(frappe.local, "request", None):
        return

    window = int(time.time()) // _USER_RL_SECONDS
    key = frappe.cache.make_key(f"crema:rl:{frappe.session.user}:{window}")
    count = frappe.cache.incrby(key, 1)
    if count == 1:
        frappe.cache.expire(key, _USER_RL_SECONDS)
    if count > _USER_RL_LIMIT:
        raise frappe.RateLimitExceededError(
            f"Rate limit exceeded: {_USER_RL_LIMIT} crema HTTP calls per hour per user"
        )


def _error_body(exc: CremaBlockedError | CremaConfigError | CremaBudgetError) -> dict:
    """{"blocked", "reason"} shared by every caller that shapes these three exceptions
    instead of letting them escape. `blocked` stays reserved for an actual
    security-guard block."""
    return {"blocked": isinstance(exc, CremaBlockedError), "reason": str(exc)}


def _error_response(exc: CremaBlockedError | CremaConfigError | CremaBudgetError) -> dict:
    """417 body shared by ask_api/extract_api/transform_api for the three exceptions
    they shape rather than let escape.

    All three are frappe.ValidationError subclasses, so frappe would answer 417
    either way — but CremaConfigError/CremaBudgetError are raised with a bare
    `raise`, not frappe.throw, so frappe never populates `_server_messages` for
    them, and the desk JS's 417 handler renders nothing on its own. Without this,
    an unshaped one reaches the browser as an empty body: a cleared spinner and no
    message at all.
    """
    frappe.local.response["http_status_code"] = 417
    return _error_body(exc)


@frappe.whitelist()
@rate_limit(limit=60, seconds=3600)
def ask_api(interface: str, prompt: str, context: str | None = None, response_json: bool = False) -> dict:
    """HTTP entry point for `ask()` — `POST /api/method/crema.api.ask_api`.

    Open to either role (frappe.only_for treats the tuple as OR — any one of the
    listed roles is enough). Two rate limits apply, both 60 calls/hour: the
    decorator's, which is per client IP, and _check_user_rate_limit's, which is per
    session user. Neither applies to in-process callers.

    Internal interfaces (`advanced_ocr`) are refused here — crema drives them itself
    through ocr()'s escalation, never over HTTP.

    A blocked prompt, a budget stop, or a config error does not propagate as a
    generic 500/417 error page: all three are caught here (see _error_response) and
    turned into a structured 417 body `{"blocked": bool, "reason": ...}` so HTTP
    callers can branch on it without parsing frappe's exception envelope or relying
    on _server_messages — CremaConfigError/CremaBudgetError are raised with a bare
    `raise`, not frappe.throw, so frappe never populates that field for them.

    `response_json=True` requests `response_format={"type": "json_object"}` from the
    model (same flag ask_json sets internally) — the result is still returned as a
    string under `result`; the caller parses it. No new permission surface: this only
    changes what shape the model is asked to answer in.
    """
    frappe.only_for(("System Manager", "Crema User"))
    if interface in interfaces.INTERNAL:
        frappe.throw(
            _("'{0}' is an internal crema interface and cannot be called over HTTP").format(interface)
        )
    _check_user_rate_limit()

    kw = {"response_format": {"type": "json_object"}} if response_json else {}
    try:
        result = ask(interface, prompt, context=context, **kw)
    except (CremaBlockedError, CremaConfigError, CremaBudgetError) as exc:
        return _error_response(exc)
    return {"result": result}


@frappe.whitelist()
@rate_limit(limit=60, seconds=3600)
def extract_api(doctype: str, file_url: str, instruction: str | None = None) -> dict:
    """HTTP entry point for `extract()` — `POST /api/method/crema.api.extract_api`.

    Same gates as ask_api: either role, both rate limits (per client IP and per
    session user — the latter shared with ask_api's budget), CremaBlockedError ->
    structured 417 body. No `interface` parameter, so ask_api's internal-interface
    refusal does not apply here.

    A File URL only — raw bytes are a Python-caller affordance, not an HTTP one. The
    isinstance check is explicit rather than annotation-implied: this module has
    `from __future__ import annotations`, which makes every annotation a string, and
    frappe's whitelisted-method type validation skips string annotations.
    """
    frappe.only_for(("System Manager", "Crema User"))
    _check_user_rate_limit()
    if not isinstance(file_url, str):
        frappe.throw(_("file_url must be a File URL string"))

    try:
        return extract(doctype, file_url, instruction)
    except (CremaBlockedError, CremaConfigError, CremaBudgetError) as exc:
        return _error_response(exc)


_EXTRACT_ASYNC_TIMEOUT = 900  # comfortably above the ~360s worst case: ocr, its
# advanced_ocr escalation, then the extraction call, each retried once (client.py's
# num_retries=1) at a provider's own timeout_seconds (default 60, no ceiling).


@frappe.whitelist()
@rate_limit(limit=60, seconds=3600)
def extract_async(
    doctype: str, file_url: str, instruction: str | None = None, request_id: str | None = None
) -> str:
    """Background HTTP entry point for `extract()` — `POST /api/method/crema.api.extract_async`.
    For extract() specifically: unlike ask()/transform(), which are one provider call the
    user is already watching, extract() can hold a web worker for minutes (see
    _EXTRACT_ASYNC_TIMEOUT) — long enough to starve other users on a small bench, not just
    the one waiting.

    Same gates as extract_api, applied here rather than in run_extract, the job this
    queues: _check_user_rate_limit is a no-op outside a real HTTP request
    (frappe.local.request is unset in a worker), so checking it there instead would let
    every queued call skip the per-user budget entirely.

    Returns immediately with `request_id` (client-supplied, so the caller can generate
    it before the request and use it to open a listener first — or generated here if
    omitted) instead of a result: the answer arrives later as a `crema_extract`
    frappe.realtime event carrying the same request_id, published by run_extract.
    """
    frappe.only_for(("System Manager", "Crema User"))
    _check_user_rate_limit()
    if not isinstance(file_url, str):
        frappe.throw(_("file_url must be a File URL string"))

    request_id = request_id or frappe.generate_hash(length=8)
    frappe.enqueue(
        "crema.api.run_extract",
        doctype=doctype,
        file_url=file_url,
        instruction=instruction,
        request_id=request_id,
        queue="long",
        timeout=_EXTRACT_ASYNC_TIMEOUT,
        job_id=f"crema-extract-{request_id}",
        deduplicate=True,
    )
    return request_id


def run_extract(doctype: str, file_url: str, instruction: str | None, request_id: str) -> None:
    """The job extract_async queues. Never whitelisted — frappe.enqueue resolves this
    dotted path itself, and a whitelisted twin would be a second, ungated entry point
    into extract() with none of extract_async's checks.

    Publishes to the ENQUEUING user's own room, never a bare room-less event: an extract
    result carries document content, and frappe.publish_realtime with no user/room/doctype
    falls through to the whole site (frappe.realtime.get_site_room). `user` below is
    exactly right for that because frappe.enqueue's execute_job calls
    frappe.set_user(user) with the session user it captured at enqueue time, before this
    function runs — the same identity extract()'s own permission checks then run under.
    """
    user = frappe.session.user
    try:
        result = extract(doctype, file_url, instruction)
    except (CremaBlockedError, CremaConfigError, CremaBudgetError) as exc:
        frappe.publish_realtime(
            "crema_extract", {"request_id": request_id, "ok": False, **_error_body(exc)}, user=user
        )
        return
    except Exception:
        # A job that dies silently leaves the desk waiting forever — publish a generic,
        # non-leaking failure before the re-raise that lands it in the RQ failed registry
        # and frappe's own error log (crema.automation's equivalent: _describe/_plain,
        # which this skips — that redaction is for text stored back onto a doctype field,
        # not for a message about to leave the server over the wire).
        frappe.log_error(title=f"Crema extract failed: {request_id}"[:140])
        frappe.publish_realtime(
            "crema_extract",
            {
                "request_id": request_id,
                "ok": False,
                "blocked": False,
                "reason": _("Crema could not read this document."),
            },
            user=user,
        )
        raise
    frappe.publish_realtime(
        "crema_extract", {"request_id": request_id, "ok": True, "result": result}, user=user
    )


@frappe.whitelist()
@rate_limit(limit=60, seconds=3600)
def transform_api(doctype: str, name: str, instruction: str) -> dict:
    """HTTP entry point for `transform()` — `POST /api/method/crema.api.transform_api`.

    Same gates as ask_api/extract_api: either role, both rate limits (per client IP and
    per session user — the hourly per-user budget is now shared across all three HTTP
    endpoints), CremaBlockedError -> structured 417 body.
    """
    frappe.only_for(("System Manager", "Crema User"))
    _check_user_rate_limit()

    try:
        return transform(doctype, name, instruction)
    except (CremaBlockedError, CremaConfigError, CremaBudgetError) as exc:
        return _error_response(exc)


@frappe.whitelist()
@rate_limit(limit=60, seconds=3600)
def ocr_api(file_url: str) -> dict:
    """HTTP entry point for `ocr()` — `POST /api/method/crema.api.ocr_api`.

    Same gates as extract_api: either role, both rate limits (per client IP and per
    session user), CremaBlockedError/CremaConfigError/CremaBudgetError -> structured
    417 body. No `instruction` parameter — that stays a Python-caller affordance,
    since this path deliberately skips layer 1 and layer 2 (see _ocr.py).

    A File URL only — raw bytes are a Python-caller affordance, not an HTTP one.
    """
    frappe.only_for(("System Manager", "Crema User"))
    _check_user_rate_limit()
    if not isinstance(file_url, str):
        frappe.throw(_("file_url must be a File URL string"))

    try:
        return ocr(file_url)
    except (CremaBlockedError, CremaConfigError, CremaBudgetError) as exc:
        return _error_response(exc)


@frappe.whitelist()
def get_models(provider: str) -> list[str]:
    """Feeds the Model Autocomplete on the Crema Settings Model Assignments grid."""
    frappe.only_for("System Manager")
    return client.list_models(provider)


@frappe.whitelist()
def check_provider(provider: str) -> dict[str, Any]:
    """Live connection status for the Connection column on the Crema Provider list.
    {"ok": bool, "reachable": bool, "detail": str} — see client.check_connection.
    "reachable" separates an endpoint that could not be reached at all from one that
    answered but rejected or errored; health() reports all three. Deliberately not
    cached, unlike get_models/list_models: a stale "Connected" pill after a key was
    revoked is worse than one extra request per page load."""
    frappe.only_for("System Manager")
    return client.check_connection(provider)


@frappe.whitelist()
def grant_crema_role(user: str | None = None) -> str:
    """Add the "Crema User" role to `user` (the caller by default).

    The one-click fix behind the notice on Crema Settings for an admin who cannot see the
    Ask Crema button. System Manager only, because handing out a role is exactly the kind
    of thing a Crema User must not be able to do for themselves."""
    frappe.only_for("System Manager")
    user = user or frappe.session.user
    frappe.get_doc("User", user).add_roles("Crema User")
    return user


@frappe.whitelist()
def get_interfaces() -> list[dict[str, str]]:
    """Relabels the AI Profile picker on Crema Automation Task: {"value", "label"} pairs for
    every selectable interface. The values themselves are already in the doctype's meta
    (install.sync_interface_options) — this exists only because a frappe Select option
    string cannot carry a label separate from its value. Labels come from interfaces.LABELS,
    which is core-only, so an app-registered interface shows its raw key."""
    frappe.only_for("System Manager")
    return [{"value": name, "label": interfaces.LABELS.get(name, name)} for name in interfaces.selectable()]


@frappe.whitelist()
def get_guardrails() -> list[dict[str, str]]:
    """Relabels the Guardrail picker on Crema Guardrails and fills the form's check
    list: {"value", "label", "help"} per registered guardrail. The values are already
    in the doctype's meta (install.sync_guardrail_options) — this exists because a
    frappe Select option string cannot carry a label separate from its value, and the
    module's own one-line `help` has no other way onto the form."""
    frappe.only_for("System Manager")
    rows = []
    for key in guardrails.registry():
        module = guardrails._module(key)
        rows.append(
            {
                "value": key,
                "label": getattr(module, "label", None) or key,
                "help": getattr(module, "help", "") or "",
            }
        )
    return rows


@frappe.whitelist()
def get_usage() -> dict[str, Any]:
    """Month-to-date spend and effective budget for every interface — feeds the
    Usage column on Crema Settings' Model Assignments grid. budget 0 means
    unlimited. A provider carries no budget of its own (that ceiling moved to the
    gateway), so this reports interfaces only.

    {"interfaces": {name: {"spend": float, "budget": float}}}
    """
    frappe.only_for("System Manager")
    settings = frappe.get_cached_doc("Crema Settings")
    spend = _log_mod.month_spend()

    interfaces_usage = {
        row.interface: {
            "spend": spend["interface"].get(row.interface, 0),
            "budget": row.monthly_budget_usd or settings.default_monthly_budget_usd or 0,
        }
        for row in settings.assignments
    }
    return {"interfaces": interfaces_usage}


@frappe.whitelist()
def run_automation_now(task: str) -> str:
    """ "Run Now" on a Crema Automation Task — always enqueues (never runs inline), so the
    manual path is byte-identical to the scheduled one. Returns the job id."""
    frappe.only_for("System Manager")
    if not frappe.db.exists("Crema Automation Task", task):
        frappe.throw(_("No Crema Automation Task named '{0}'").format(task))

    from crema import automation

    return automation.enqueue_task(task)


def _webhook_signature(secret: str, task: str, timestamp: str, payload: str) -> str:
    """The HMAC a signed trigger_automation call must carry — hex SHA-256 over
    "{timestamp}.{task}.{payload}", keyed by the task's own webhook_secret."""
    message = f"{timestamp}.{task}.{payload}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _check_webhook_signature(
    doc, task: str, payload: str, timestamp: str | None, signature: str | None
) -> None:
    """The optional half of trigger_automation's auth: while `doc.webhook_secret` is set,
    every call must carry a fresh, matching, not-yet-seen signature. Empty secret means
    unchanged behaviour — Frappe token auth alone, as before this existed.

    Verified against `payload` as received, before automation truncates it, because that
    is what the caller actually signed.
    """
    secret = doc.get_password("webhook_secret", raise_exception=False)
    if not secret:
        return

    if not timestamp or not signature:
        frappe.throw(_("'{0}' requires a signed call.").format(task), frappe.PermissionError)

    try:
        age = time.time() - int(timestamp)
    except ValueError:
        frappe.throw(_("'{0}': timestamp is not a number.").format(task), frappe.PermissionError)
    if abs(age) > _WEBHOOK_WINDOW_SECONDS:
        frappe.throw(_("'{0}': timestamp is too old or in the future.").format(task), frappe.PermissionError)

    expected = _webhook_signature(secret, task, timestamp, payload)
    if not hmac.compare_digest(expected, signature):
        frappe.throw(_("'{0}': signature does not match.").format(task), frappe.PermissionError)

    nonce_key = frappe.cache.make_key(cache.webhook_nonce_key(signature))
    # nosemgrep: frappe-cache-breaks-multitenancy — make_key above scopes the key per site
    if not frappe.cache.set(nonce_key, 1, nx=True, ex=_WEBHOOK_NONCE_TTL_SECONDS):
        frappe.throw(_("'{0}': this call was already used.").format(task), frappe.PermissionError)


@frappe.whitelist()
@rate_limit(limit=60, seconds=3600)
def trigger_automation(
    task: str, payload: str | None = None, timestamp: str | None = None, signature: str | None = None
) -> str:
    """Start a Crema Automation Task from outside the site. Returns the job id.

    Authentication is frappe's own — an API key and secret in the Authorization header,
    nothing crema-specific. The fence is frappe's permission engine: the caller needs read
    access to this task document, which an ordinary role grants, so an integration never
    needs System Manager (which run_automation_now does require, since it can start *any*
    task including one that was never meant to be reachable from outside).

    Only a task whose Trigger is Webhook can be started this way, and only while it is
    enabled — a scheduled task is not remotely pokeable by anyone who can merely read it.

    `payload` is optional text read as one more source. It is truncated here and scanned by
    layer 1 downstream like any other content.

    While the task carries a Webhook Secret, `timestamp` and `signature` are also required
    — see `_check_webhook_signature`. A task with no secret set accepts a call exactly as
    before this existed.
    """
    doc = frappe.get_doc("Crema Automation Task", task)
    doc.check_permission("read")

    if doc.trigger != "Webhook":
        frappe.throw(_("'{0}' is not a Webhook task.").format(task), frappe.PermissionError)
    if not doc.enabled:
        frappe.throw(_("'{0}' is switched off.").format(task), frappe.PermissionError)

    _check_webhook_signature(doc, task, payload or "", timestamp, signature)

    from crema import automation

    payload = (payload or "")[: automation._WEBHOOK_PAYLOAD_CHARS] or None
    return automation.enqueue_task(task, payload=payload)


@frappe.whitelist()
@rate_limit(limit=20, seconds=3600)
def dry_run_automation(task: str) -> dict:
    """ "Dry Run" on a Crema Automation Task — reads the source, plans, extracts, and
    stops before any write. Runs inline (unlike run_automation_now, which enqueues)
    because the caller is waiting on the result; `@rate_limit` is what bounds that, since
    there is no enqueue-dedup to collapse repeats.
    """
    frappe.only_for("System Manager")
    if not frappe.db.exists("Crema Automation Task", task):
        frappe.throw(_("No Crema Automation Task named '{0}'").format(task))

    from crema import automation

    try:
        return automation.dry_run(task)
    except (CremaBlockedError, CremaConfigError, CremaBudgetError) as exc:
        return _error_response(exc)


def _proposal_names(names: str | list[str]) -> list[str]:
    return frappe.parse_json(names) if isinstance(names, str) else names


@frappe.whitelist()
def approve_proposals(names: str | list[str]) -> list[dict]:
    """Approve one or more `Crema Proposal` rows — the form's Approve button and the
    list view's bulk action both call this. Each name is independent: one bad row must
    not stop the rest from going through. Returns the failures only, as
    `[{"name", "error"}, ...]`; an empty list is every row approved."""
    frappe.only_for("System Manager")
    from crema import automation

    failures = []
    for name in _proposal_names(names):
        try:
            automation.apply_proposal(name)
        except Exception as exc:
            failures.append({"name": name, "error": _log_mod.redact(str(exc))})
    return failures


@frappe.whitelist()
def discard_proposals(names: str | list[str]) -> list[dict]:
    """Discard one or more `Crema Proposal` rows. See `approve_proposals` for the shape
    of what this returns."""
    frappe.only_for("System Manager")
    from crema import automation

    failures = []
    for name in _proposal_names(names):
        try:
            automation.discard_proposal(name)
        except Exception as exc:
            failures.append({"name": name, "error": _log_mod.redact(str(exc))})
    return failures


@frappe.whitelist()
def undo_proposals(names: str | list[str]) -> list[dict]:
    """Reverse one or more Approved `Crema Proposal` rows: delete what approving them
    created and return each to Pending. See `approve_proposals` for the shape of what
    this returns."""
    frappe.only_for("System Manager")
    from crema import automation

    failures = []
    for name in _proposal_names(names):
        try:
            automation.undo_proposal(name)
        except Exception as exc:
            failures.append({"name": name, "error": _log_mod.redact(str(exc))})
    return failures


@frappe.whitelist()
def undo_last_run(task: str) -> dict:
    """Delete every record a Crema Automation Task's last run created, leaving every
    record it updated for manual review — see PLAN.md's "Undo a run"."""
    frappe.only_for("System Manager")
    from crema import automation

    try:
        return automation.undo_last_run(task)
    except Exception as exc:
        frappe.throw(_log_mod.redact(str(exc)))
