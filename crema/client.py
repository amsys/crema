"""litellm client boundary — the only place a provider's api_key is ever touched.

No public function here accepts a model/provider/api_key parameter; callers only ever
pass an interface name (see crema/api.py). That is the "direct provider calls
impossible" boundary described in the app plan.
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Any

import requests

import frappe
from crema import cache, interfaces
from crema import log as _log
from crema._json import strip_fence
from crema.exceptions import CremaBlockedError, CremaConfigError
from frappe.utils.caching import redis_cache


def _assignment_row(name: str):
    """The Crema Model Assignment child row for `name`, or None. frappe.get_cached_doc
    keeps this off the DB on every call — the Single is invalidated wholesale by
    CremaSettings.on_update (cache.clear_interfaces), same cheap "nuke and rebuild"
    trade-off client.py already made for providers."""
    settings = frappe.get_cached_doc("Crema Settings")
    return next((row for row in settings.assignments if row.interface == name), None)


def _load_from_db(name: str) -> dict[str, Any] | None:
    """Load a resolved (no api_key) config for `name` if it's a configured interface
    with an enabled provider. Returns None if the interface doesn't exist or its
    effective provider (row override, else Crema Settings.default_provider) is
    missing/disabled. Same coalesce for model/isolation_user — a row overrides only
    what it sets; everything else falls through to the matching default."""
    settings = frappe.get_cached_doc("Crema Settings")
    doc = _assignment_row(name)
    if doc is None:
        return None

    provider_name = doc.provider or settings.default_provider
    if not provider_name or not frappe.db.exists("Crema Provider", provider_name):
        return None

    provider = frappe.get_doc("Crema Provider", provider_name)
    if not provider.enabled:
        return None

    return {
        "interface": doc.interface,
        "provider": provider.provider_name,
        "base_url": provider.base_url,
        "timeout_seconds": provider.timeout_seconds,
        "model": doc.model or settings.default_model,
        "system_prompt": doc.system_prompt,
        "isolation_user": doc.isolation_user or settings.default_isolation_user,
        "enable_prompt_scan": bool(doc.enable_prompt_scan),
        "enable_llm_guard": bool(doc.enable_llm_guard),
        "output_trap": doc.output_trap or "Off",
        "temperature": doc.temperature,
        "cache_ttl": doc.cache_ttl or 0,
        "monthly_budget_usd": doc.monthly_budget_usd or settings.default_monthly_budget_usd or 0,
        "provider_budget_usd": provider.monthly_budget_usd or 0,
    }


def _resolve_one(name: str) -> dict[str, Any] | None:
    """Cached lookup for a single interface name — no fallback walking here."""
    key = cache.interface_key(name)
    cached = frappe.cache.get_value(key)
    if cached is not None:
        return cached

    cfg = _load_from_db(name)
    if cfg is not None:
        frappe.cache.set_value(key, cfg)
    return cfg


def _prompt_for(interface: str) -> str:
    """The requested interface's own prompt, even when it isn't configured — its
    seeded row already carries DEFAULT_PROMPTS (CremaModelAssignment.validate)."""
    row = _assignment_row(interface)
    return (row and row.system_prompt) or interfaces.DEFAULT_PROMPTS.get(interface, "")


def _resolve(interface: str) -> dict[str, Any]:
    """Resolve an interface name to a config dict, walking interfaces.FALLBACKS
    until a configured interface with an enabled provider is found.

    "advanced_ocr" and "security" have no fallback entry — an unresolved
    "advanced_ocr" means escalation is silently skipped by the caller; an
    unresolved "security" is a fail-loud admin misconfiguration.
    """
    name = interface
    seen: set[str] = set()
    while True:
        cfg = _resolve_one(name)
        if cfg is not None:
            if name != interface:
                # The fallback supplies provider/model/isolation_user only. The
                # requested interface's prompt is its contract -- view/transform/
                # extraction emit strict JSON their callers parse, and complex's
                # generic prompt would yield unparseable prose instead.
                cfg = {**cfg, "system_prompt": _prompt_for(interface)}
            return cfg

        seen.add(name)
        fallback = interfaces.FALLBACKS.get(name)
        if fallback is None or fallback in seen:
            raise CremaConfigError(f"No usable configuration found for crema interface '{interface}'")
        name = fallback


def _api_key(provider: str) -> str | None:
    """Decrypted provider api_key, memoized request-local only (never redis)."""
    if not hasattr(frappe.local, "crema_keys"):
        frappe.local.crema_keys = {}

    if provider not in frappe.local.crema_keys:
        doc = frappe.get_doc("Crema Provider", provider)
        frappe.local.crema_keys[provider] = doc.get_password("api_key", raise_exception=False)

    return frappe.local.crema_keys[provider]


def _as_number(value: Any) -> float:
    """`value` if it's a real int/float, else 0.

    test_client.py's test_complete_builds_expected_litellm_kwargs patches
    litellm.completion with a bare MagicMock — response.usage then auto-vivifies as
    another MagicMock, and MagicMock configures numeric dunders (__float__, __int__)
    with harmless-looking non-zero defaults rather than raising, so a try/except
    float() coercion would silently count fake tokens. An explicit isinstance check
    is the only thing that actually distinguishes a real number from a test double.
    """
    if isinstance(value, bool):  # bool is an int subclass; never a token/cost count
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _record_usage(response: Any) -> None:
    """Accumulate this call's usage onto frappe.local, request-local like
    _api_key's frappe.local.crema_keys memo. crema.log.insert drains and clears this
    on every logged row, so it never leaks between calls or across requests."""
    if not hasattr(frappe.local, "crema_usage"):
        frappe.local.crema_usage = {
            "llm_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": 0.0,
        }

    usage = getattr(response, "usage", None)
    hidden = getattr(response, "_hidden_params", None) or {}

    acc = frappe.local.crema_usage
    acc["llm_calls"] += 1
    acc["prompt_tokens"] += int(_as_number(getattr(usage, "prompt_tokens", 0)))
    acc["completion_tokens"] += int(_as_number(getattr(usage, "completion_tokens", 0)))
    acc["cost_usd"] += _as_number(hidden.get("response_cost"))


# Layer 3 (the output trap): a per-call random nonce the model is told to echo back,
# in a fixed position. Content-agnostic — unlike layer 1 (security.scan) and layer 2
# (the LLM guard), it doesn't inspect the input at all, so it is the only layer that
# also covers crema._ocr's calls, which bypass layers 1 and 2 on purpose (a regex/LLM
# scan tuned for user prompts false-positives on arbitrary document text).
#
# Missing nonce -> the model stopped following crema's system prompt (hijack).
# Nonce present but ALSO echoed elsewhere in the body -> the model is leaking its
# system prompt back into its answer.
_TRAP_TEXT = "Finish your reply with this exact token alone on the final line, nothing after it: {nonce}"
_TRAP_JSON = (
    'The JSON object you return must also contain the key "_crema" with the exact string value "{nonce}".'
)


def _arm_trap(messages: list[dict], nonce: str, response_format: dict | None) -> list[dict]:
    """Return a NEW messages list (never mutates `messages`) with the layer-3
    instruction appended to the system message, or prepended as a new one if
    `messages[0]` isn't role=system."""
    instruction = (_TRAP_JSON if response_format else _TRAP_TEXT).format(nonce=nonce)
    if messages and messages[0].get("role") == "system":
        armed = {**messages[0], "content": f"{messages[0]['content']}\n\n{instruction}"}
        return [armed, *messages[1:]]
    return [{"role": "system", "content": instruction}, *messages]


def _spring_trap(content: str, nonce: str, response_format: dict | None) -> tuple[str, str | None]:
    """Check `content` for the layer-3 nonce and strip it out. Returns
    (best-effort-cleaned body, miss_reason | None) — never raises; `_complete` decides
    what a miss means (log it, retry it, block it) per the interface's `output_trap`
    setting.

    JSON mode: the returned object must carry `{"_crema": nonce}`; that key is always
    popped before the body is handed back, whether or not it matched, so a caller
    never sees the trap's own plumbing. Every response_format call site in this app
    uses free-form `{"type": "json_object"}`, so an extra key is legal everywhere.

    Text mode: the last non-empty line must equal the nonce exactly.

    Either mode: if the nonce still appears anywhere in the cleaned body, that's a
    prompt-prompt leak, reported as its own reason even though the nonce was found in
    the expected place too.
    """
    if response_format:
        try:
            parsed = json.loads(strip_fence(content))
        except json.JSONDecodeError, TypeError:
            return content, "trap: response not valid JSON"
        if not isinstance(parsed, dict):
            return content, "trap: response is not a JSON object"
        found = parsed.pop("_crema", None)
        body = json.dumps(parsed)
        if found != nonce:
            return body, "trap: nonce missing"
    else:
        lines = content.rstrip().splitlines()
        if not lines or lines[-1].strip() != nonce:
            return content, "trap: nonce missing"
        body = "\n".join(lines[:-1]).rstrip()

    if nonce in body:
        return body, "trap: nonce echoed in body"
    return body, None


def _record_trap_miss(reason: str) -> None:
    """Stash a non-blocking (Log Only) trap miss onto frappe.local, request-local like
    _record_usage's crema_usage accumulator. crema.log.insert drains and clears this on
    every logged row, folding it into that row's `detail` — so a Log Only miss is still
    visible in the audit trail without changing the row's status away from Success."""
    frappe.local.crema_trap = reason


def _complete(cfg: dict[str, Any], messages: list[dict], response_format: dict | None = None) -> str:
    """The only place a provider is actually called."""
    import litellm

    def _call_once(msgs: list[dict]) -> str:
        kwargs: dict[str, Any] = {
            "model": f"openai/{cfg['model']}",
            "messages": msgs,
            "api_base": cfg["base_url"],
            "api_key": _api_key(cfg["provider"]),
            "timeout": cfg["timeout_seconds"],
            "temperature": cfg["temperature"],
            "num_retries": 1,
        }
        if response_format:
            kwargs["response_format"] = response_format

        response = litellm.completion(**kwargs)
        _record_usage(response)
        return response.choices[0].message.content

    trap = cfg.get("output_trap") or "Off"
    if trap == "Off":
        return _call_once(messages)

    nonce = secrets.token_hex(8)
    body, miss = _spring_trap(_call_once(_arm_trap(messages, nonce, response_format)), nonce, response_format)

    if miss is None or trap == "Log Only":
        if miss:
            _record_trap_miss(miss)
        return body

    if trap == "Retry Once":
        nonce = secrets.token_hex(8)
        body, miss = _spring_trap(
            _call_once(_arm_trap(messages, nonce, response_format)), nonce, response_format
        )
        if miss is None:
            return body
        # Second miss falls through to Block below.

    raise CremaBlockedError(miss)


# A provider's /models response is untrusted third-party input that ends up in a
# System Manager's Desk session (crema_settings.js feeds it to an Autocomplete, which
# Awesomplete renders via innerHTML). Real-world model ids look like `gpt-4o`,
# `meta-llama/llama-3.1-8b-instruct:free`, `qwen2.5-coder:7b` — so anything outside
# this conservative alphabet is dropped rather than escaped.
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/@+-]{1,128}$")


def _fetch_models(provider: str) -> tuple[list[str], str | None]:
    """(ids, error). Shared by list_models (cached, ids only) and check_connection
    (uncached, surfaces the error) — litellm has no generic list-models call, so this
    hits the OpenAI-compatible GET {base_url}/models directly. Ids that don't match
    _MODEL_ID_RE are silently dropped from the ids list but don't affect success."""
    if not frappe.db.exists("Crema Provider", provider):
        return [], "No such provider"

    doc = frappe.get_doc("Crema Provider", provider)
    base_url = (doc.base_url or "").rstrip("/")
    if not base_url:
        return [], "No Base URL configured"

    api_key = doc.get_password("api_key", raise_exception=False)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    # log.redact only strips keys it finds in frappe.local.crema_keys, which _api_key
    # populates — but this function reads the key directly via get_password rather
    # than going through _api_key (list_models/check_connection have no "interface" to
    # resolve a provider from, so there's no cfg to hand _complete's usual path). Feed
    # the memo here too, or a provider that echoes its key back in an error body (a
    # bad-request response, a proxy's debug page) would leak it into the uncached
    # Providers-panel connection check.
    if api_key:
        if not hasattr(frappe.local, "crema_keys"):
            frappe.local.crema_keys = {}
        frappe.local.crema_keys[provider] = api_key

    try:
        response = requests.get(f"{base_url}/models", headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json().get("data", [])
        ids = [
            m["id"]
            for m in data
            if isinstance(m, dict) and isinstance(m.get("id"), str) and _MODEL_ID_RE.match(m["id"])
        ]
        return ids, None
    except Exception as exc:
        return [], _log.redact(f"{type(exc).__name__}: {exc}")


@redis_cache(ttl=3600)
def list_models(provider: str) -> list[str]:
    """Model ids available from `provider`. [] on any error."""
    ids, _error = _fetch_models(provider)
    return ids


def check_connection(provider: str) -> dict[str, Any]:
    """Live (uncached — an hour-stale status is worse than none) connectivity check
    for the Providers panel on Crema Settings. {"ok": bool, "detail": str}: detail is
    "<n> models" on success, else the redacted error _fetch_models returned."""
    ids, error = _fetch_models(provider)
    if error is None:
        return {"ok": True, "detail": f"{len(ids)} models"}
    return {"ok": False, "detail": error}
