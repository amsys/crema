"""Public API — the only entry surface into crema.

No public function anywhere accepts a model/provider/api_key parameter; that
parameter only exists inside client._complete. Direct provider calls are
structurally impossible from caller code.
"""

from __future__ import annotations

import functools
import hashlib
import json
import time
from typing import Any

import frappe
from crema import _ocr as _ocr_impl
from crema import cache, client, interfaces, sandbox, security
from crema import log as _log_mod
from crema._json import strip_fence
from crema.exceptions import CremaBlockedError, CremaBudgetError, CremaConfigError
from frappe.rate_limiter import rate_limit

__all__ = [
    "CremaBlockedError",
    "CremaBudgetError",
    "CremaConfigError",
    "ask",
    "ask_api",
    "ask_json",
    "extract",
    "extract_api",
    "get_interfaces",
    "get_models",
    "get_usage",
    "ocr",
    "transform",
    "transform_api",
]

# Interfaces that only crema itself may drive. `security` is the layer-2 classifier —
# exposing it over HTTP hands a caller a jailbreak-calibration oracle; `advanced_ocr`
# is an internal escalation target reached through ocr(), never addressed directly.
_INTERNAL_INTERFACES = frozenset({"security", "advanced_ocr"})

_USER_RL_LIMIT = 60
_USER_RL_SECONDS = 3600

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


def _prompt_hash(cfg: dict[str, Any], prompt: str, context: str | None, response_format: dict | None) -> str:
    parts = "|".join(
        [
            cfg["interface"],
            cfg.get("model") or "",
            cfg.get("system_prompt") or "",
            prompt,
            context or "",
            json.dumps(response_format, sort_keys=True) if response_format else "",
        ]
    )
    return hashlib.sha256(parts.encode()).hexdigest()


def _user_content(prompt: str, context: str | None) -> str:
    """The exact string the model receives as the user turn.

    Both security layers scan THIS, not prompt and context separately: the model sees
    them concatenated, so an injection split across the two fields ("...please ignore
    all" in context, "previous instructions..." in prompt) is only visible here.
    Scanning the concatenation is strictly stronger than scanning each field, since
    each field remains a substring of it.
    """
    return f"<context>\n{context}\n</context>\n{prompt}" if context else prompt


def _build_messages(cfg: dict[str, Any], prompt: str, context: str | None) -> list[dict]:
    return [
        {"role": "system", "content": cfg.get("system_prompt") or ""},
        {"role": "user", "content": _user_content(prompt, context)},
    ]


def _resolve_files(files: list[str]) -> list[dict]:
    """Permission-checked (as the isolation user) content parts for vision models.

    Delegates to crema._ocr.prep_parts, which routes PDFs through the same text-vs-scan
    detection ocr() uses (extracted text for text PDFs, rendered page images for
    scanned ones) and downscales plain images. Anything else is dropped.
    """
    import mimetypes

    parts: list[dict] = []
    for file_url in files:
        try:
            file_doc = frappe.get_doc("File", {"file_url": file_url})
            file_doc.check_permission("read")
            content = file_doc.get_content()
        except Exception:
            continue

        content = content if isinstance(content, bytes) else content.encode()
        mime = mimetypes.guess_type(file_doc.file_name or file_url)[0] or ""
        parts.extend(_ocr_impl.prep_parts(content, mime))
    return parts


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
    through the exact same `__call__` flow below (security scan, cache, LLM guard,
    sandboxed completion) — the sugar changes nothing about what runs. A single
    positional argument, `ask("text")`, is a third equivalent form: it runs the
    `simple` interface, same as `ask("simple", "text")`.
    """

    def __call__(
        self,
        interface: str,
        prompt: str | None = None,
        *,
        context: str | None = None,
        files: list[str] | None = None,
        response_format: dict | None = None,
        cache_ttl: int | None = None,
    ) -> str:
        """Ask a configured interface. See the app plan for the full ordered flow.

        Called with one positional argument, that argument is the prompt and the
        interface defaults to `simple` — `interfaces.LABELS` calls it "Default" and
        it's the end of every fallback chain.

        `cache_ttl`, if not None, overrides the interface's configured cache_ttl for
        this call only (e.g. to force-cache a call whose interface has caching off).
        No model/provider/api_key override exists here or anywhere public — that
        boundary is intentional, see the module docstring.
        """
        if prompt is None:
            interface, prompt = "simple", interface

        cfg = client._resolve(interface)
        if cache_ttl is not None:
            cfg = {**cfg, "cache_ttl": cache_ttl}

        prompt_sha = _prompt_hash(cfg, prompt, context, response_format)
        user_content = _user_content(prompt, context)

        if cfg["enable_prompt_scan"]:
            # scan() joins context+prompt and scans the join — see its docstring for why
            # it is handed the two fields rather than the delimiter-wrapped user_content
            # that layer 2 and the provider get.
            reason = security.scan(prompt, context)
            if reason:
                _log(cfg, "Blocked", reason, prompt_sha=prompt_sha)
                raise CremaBlockedError(reason)

        if cfg["cache_ttl"]:
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

        if cfg["enable_llm_guard"]:
            # phase 3: llm guard. This is defense-in-depth, not the hard fence — layer 1
            # (scan, above) and User Permissions on the isolation user are. A guard
            # error or unparseable response therefore fails OPEN (proceeds), by design.
            risk = None
            try:
                guard_raw = self("security", user_content)
                risk = json.loads(strip_fence(guard_raw)).get("risk")
            except Exception:
                _log(
                    cfg,
                    "Error",
                    "llm guard error/unparseable — proceeding (fail-open)",
                    prompt_sha=prompt_sha,
                )

            if risk == "malicious":
                _log(cfg, "Blocked", "llm guard: malicious", prompt_sha=prompt_sha)
                raise CremaBlockedError("blocked by security guard")
            if risk == "suspicious":
                _log(cfg, "Success", "llm guard: suspicious — proceeded", prompt_sha=prompt_sha)

        messages = _build_messages(cfg, prompt, context)

        start = time.monotonic()
        try:
            with sandbox.isolation(cfg["isolation_user"]):
                file_parts = _resolve_files(files) if files else []
                if file_parts:
                    messages[-1] = {
                        "role": "user",
                        "content": [{"type": "text", "text": messages[-1]["content"]}, *file_parts],
                    }
                result = client._complete(cfg, messages, response_format)
        except CremaBlockedError as exc:
            # client._complete's layer-3 output trap (an "output_trap: Block" or
            # "Retry Once" miss) raises this. Logged as Blocked, not Error, so a trap
            # hit is distinguishable from a provider failure in the audit log — that
            # distinction is what makes the trap's false-positive rate measurable.
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

        if cfg["cache_ttl"]:
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
    # </context> (see _user_content) rather than concatenated into the prompt, and layer
    # 1 (security.scan) still runs on it, same as automation._extract's fetched content.
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


def _error_response(exc: CremaBlockedError | CremaConfigError | CremaBudgetError) -> dict:
    """417 body shared by ask_api/extract_api/transform_api for the three exceptions
    they shape rather than let escape.

    All three are frappe.ValidationError subclasses, so frappe would answer 417
    either way — but CremaConfigError/CremaBudgetError are raised with a bare
    `raise`, not frappe.throw, so frappe never populates `_server_messages` for
    them, and the desk JS's 417 handler renders nothing on its own. Without this,
    an unshaped one reaches the browser as an empty body: a cleared spinner and no
    message at all. `blocked` stays reserved for an actual security-guard block.
    """
    frappe.local.response["http_status_code"] = 417
    return {"blocked": isinstance(exc, CremaBlockedError), "reason": str(exc)}


@frappe.whitelist()
@rate_limit(limit=60, seconds=3600)
def ask_api(interface: str, prompt: str, context: str | None = None, response_json: bool = False) -> dict:
    """HTTP entry point for `ask()` — `POST /api/method/crema.api.ask_api`.

    Open to either role (frappe.only_for treats the tuple as OR — any one of the
    listed roles is enough). Two rate limits apply, both 60 calls/hour: the
    decorator's, which is per client IP, and _check_user_rate_limit's, which is per
    session user. Neither applies to in-process callers.

    Internal interfaces (`security`, `advanced_ocr`) are refused here — crema drives
    them itself; a caller who could address the `security` classifier directly would
    have a jailbreak-calibration oracle.

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
    if interface in _INTERNAL_INTERFACES:
        frappe.throw(f"'{interface}' is an internal crema interface and cannot be called over HTTP")
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
        frappe.throw("file_url must be a File URL string")

    try:
        return extract(doctype, file_url, instruction)
    except (CremaBlockedError, CremaConfigError, CremaBudgetError) as exc:
        return _error_response(exc)


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
def get_models(provider: str) -> list[str]:
    """Feeds the Model Autocomplete on the Crema Settings Model Assignments grid."""
    frappe.only_for("System Manager")
    return client.list_models(provider)


@frappe.whitelist()
def check_provider(provider: str) -> dict[str, Any]:
    """Live connection status for the Providers panel on Crema Settings.
    {"ok": bool, "detail": str} — see client.check_connection. Deliberately not
    cached, unlike get_models/list_models: a stale "Connected" pill after a key was
    revoked is worse than one extra request per page load."""
    frappe.only_for("System Manager")
    return client.check_connection(provider)


@frappe.whitelist()
def get_interfaces() -> list[str]:
    """Feeds the Interface Autocomplete on Crema Automation Task — the fixed
    interfaces.PREDEFINED set, same source Crema Settings' assignments reconcile to."""
    frappe.only_for("System Manager")
    return list(interfaces.PREDEFINED)


@frappe.whitelist()
def get_usage() -> dict[str, Any]:
    """Month-to-date spend and effective budget for every interface and every
    provider — feeds the Usage column on Crema Settings' Model Assignments grid and
    the Providers panel. budget 0 means unlimited.

    {"interfaces": {name: {"spend": float, "budget": float}},
     "providers": {name: {"spend": float, "budget": float}}}
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
    providers_usage = {
        p.name: {"spend": spend["provider"].get(p.name, 0), "budget": p.monthly_budget_usd or 0}
        for p in frappe.get_all("Crema Provider", fields=["name", "monthly_budget_usd"])
    }
    return {"interfaces": interfaces_usage, "providers": providers_usage}


@frappe.whitelist()
def run_automation_now(task: str) -> str:
    """ "Run Now" on a Crema Automation Task — always enqueues (never runs inline), so the
    manual path is byte-identical to the scheduled one. Returns the job id."""
    frappe.only_for("System Manager")
    if not frappe.db.exists("Crema Automation Task", task):
        frappe.throw(f"No Crema Automation Task named '{task}'")

    from crema import automation

    return automation.enqueue_task(task)
