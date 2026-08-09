"""litellm client boundary — the only place a provider's api_key is ever touched.

No public function here accepts a model/provider/api_key parameter; callers only ever
pass an interface name (see crema/api.py). That is the "direct provider calls
impossible" boundary described in the app plan.
"""

from __future__ import annotations

import re
from typing import Any

import requests

import frappe
from crema import cache, interfaces, policy
from crema import log as _log
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
        "temperature": doc.temperature,
        "max_tokens": doc.max_tokens or 0,
        "cache_ttl": doc.cache_ttl or 0,
        "monthly_budget_usd": doc.monthly_budget_usd or settings.default_monthly_budget_usd or 0,
        "provider_budget_usd": provider.monthly_budget_usd or 0,
        "per_user_budget_usd": settings.get("default_monthly_budget_usd_per_user") or 0,
    }


def _scanner_cfg(provider_name: str | None, model: str | None) -> dict[str, Any]:
    """A bare provider config for a guardrail's own scanner call (the AI Guard) — never
    an interface's. `provider_name`/`model` come straight off the guardrail row; blank
    falls back to Crema Settings' Default Provider/Model, same coalesce _load_from_db
    uses for a Model Assignment row. A scanner is not a use case: it inherits no
    interface's budget, standing instructions or isolation identity, only a provider to
    call through. Raises CremaConfigError when the effective provider is missing or
    disabled — same fail-loud contract as _resolve, checked again at save time by
    CremaGuardrails._validate_guard."""
    settings = frappe.get_cached_doc("Crema Settings")
    resolved_name = provider_name or settings.default_provider
    if not resolved_name or not frappe.db.exists("Crema Provider", resolved_name):
        raise CremaConfigError(f"No usable AI service found for guard provider '{provider_name or ''}'")

    provider = frappe.get_doc("Crema Provider", resolved_name)
    if not provider.enabled:
        raise CremaConfigError(f"AI service '{resolved_name}' is disabled")

    return {
        "provider": provider.provider_name,
        "base_url": provider.base_url,
        "timeout_seconds": provider.timeout_seconds,
        "model": model or settings.default_model,
        "temperature": 0,
        "max_tokens": 0,
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
    seeded row already carries one (CremaModelAssignment.validate, via
    interfaces.prompt_for)."""
    row = _assignment_row(interface)
    return (row and row.system_prompt) or interfaces.prompt_for(interface)


def _resolve(interface: str) -> dict[str, Any]:
    """Resolve an interface name to a config dict, walking interfaces.fallback_for()
    until a configured interface with an enabled provider is found.

    "advanced_ocr" has no fallback entry — an unresolved "advanced_ocr" means
    escalation is silently skipped by the caller.

    Every returned config carries `requested`: the interface name the caller actually
    asked for, whatever the fallback walk resolved. The guardrails pipeline
    (crema.guardrails) filters its rows by this name, never the fallback's — so a
    fallback lends its provider, never the requested interface's security posture.

    Raises CremaConfigError when the site-wide kill switch is enabled (policy.disabled).
    """
    if policy.disabled():
        raise CremaConfigError("crema is disabled")

    name = interface
    seen: set[str] = set()
    while True:
        cfg = _resolve_one(name)
        if cfg is not None:
            if name != interface:
                # The fallback lends provider/model/isolation_user (and, per
                # api.health's contract, its billing identity). The requested
                # interface keeps its own prompt, because view/transform/extraction
                # emit strict JSON their callers parse and complex's generic prompt
                # would yield unparseable prose.
                cfg = {**cfg, "system_prompt": _prompt_for(interface)}
            return {**cfg, "requested": interface}

        seen.add(name)
        fallback = interfaces.fallback_for(name)
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


def _record_transcript(msgs: list[dict], content: str) -> None:
    """On a developer_mode site only, stash one provider round trip onto frappe.local so
    crema.log.insert can attach it to the Crema Log row as a comment.

    Request-local and drained on every logged row, exactly like _record_usage's
    crema_usage and _record_trap_miss's crema_trap. A list, not a scalar: one logged row
    can bill several round trips (an escalated OCR call, a trap retry), and all of them
    belong on it.

    This is the one place Crema keeps prompt text, and it is why the guard is
    frappe.conf.developer_mode — a production site never reaches the append, so the
    drain finds nothing and log.insert behaves as it always has. See docs/security.md.
    """
    if not frappe.conf.developer_mode:
        return
    if getattr(frappe.local, "crema_transcript", None) is None:
        frappe.local.crema_transcript = []
    frappe.local.crema_transcript.append({"messages": msgs, "response": content})


def _complete(cfg: dict[str, Any], messages: list[dict], response_format: dict | None = None) -> str:
    """The only place a provider is actually called from caller code — every call
    passes through the guardrails onion (crema.guardrails.run): masking, the AI
    guard, and the reply check wrap _call_raw here, in the row order the Guardrails
    doctype sets. Integration tests mock exactly this boundary."""
    from crema import guardrails

    return guardrails.run(cfg, messages, lambda msgs: _call_raw(cfg, msgs, response_format), response_format)


def _call_raw(cfg: dict[str, Any], messages: list[dict], response_format: dict | None = None) -> str:
    """The bare litellm.completion call — the single api_key touchpoint. No guardrail
    runs here; only crema.guardrails may call it besides _complete's own closure (the
    AI guard uses it directly, which is what makes guard recursion structurally
    impossible)."""
    import litellm

    kwargs: dict[str, Any] = {
        "model": f"openai/{cfg['model']}",
        "messages": messages,
        "api_base": cfg["base_url"],
        "api_key": _api_key(cfg["provider"]),
        "timeout": cfg["timeout_seconds"],
        "temperature": cfg["temperature"],
        "num_retries": 1,
    }
    if response_format:
        kwargs["response_format"] = response_format
    if cfg.get("max_tokens"):
        kwargs["max_tokens"] = cfg["max_tokens"]

    response = litellm.completion(**kwargs)
    _record_usage(response)
    content = response.choices[0].message.content
    _record_transcript(messages, content)
    return content


def _transcribe(
    cfg: dict[str, Any], audio: bytes, filename: str, mime: str, language: str | None = None
) -> dict[str, Any]:
    """The only place a provider's transcription endpoint is actually called —
    audio's sibling of `_complete`. No reply check: there is no system prompt to
    protect and no text instruction channel a nonce could ride along on.

    Audio bytes are the other channel masking (any guardrail with masks_text = True)
    cannot reach — there is no text to hide anything in. A masking guardrail set to
    Block refuses the call outright, same doctrine as the image-part guard; Log Only
    proceeds unmasked, silently, since there is nothing to record a miss against."""
    from crema import guardrails

    interface = cfg.get("requested") or cfg.get("interface") or "transcribe"
    if guardrails.blocks_unmaskable(interface):
        raise CremaBlockedError("masking: audio content cannot be masked")

    import litellm

    kwargs: dict[str, Any] = {
        "model": f"openai/{cfg['model']}",
        "file": (filename, audio, mime),
        "api_base": cfg["base_url"],
        "api_key": _api_key(cfg["provider"]),
        "timeout": cfg["timeout_seconds"],
        "response_format": "verbose_json",
    }
    if language:
        kwargs["language"] = language

    response = litellm.transcription(**kwargs)
    _record_usage(response)
    return {
        "text": (response.get("text") or "").strip(),
        "language": response.get("language"),
        "duration": response.get("duration"),
    }


# A provider's /models response is untrusted third-party input that ends up in a
# System Manager's Desk session (crema_settings.js feeds it to an Autocomplete, which
# Awesomplete renders via innerHTML). Real-world model ids look like `gpt-4o`,
# `meta-llama/llama-3.1-8b-instruct:free`, `qwen2.5-coder:7b` — so anything outside
# this conservative alphabet is dropped rather than escaped.
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/@+-]{1,128}$")


def _fetch_models(provider: str) -> tuple[list[str], str | None, bool]:
    """(ids, error, reachable). Shared by list_models (cached, ids only) and
    check_connection (uncached, surfaces the error) — litellm has no generic
    list-models call, so this hits the OpenAI-compatible GET {base_url}/models
    directly. Ids that don't match _MODEL_ID_RE are silently dropped from the ids
    list but don't affect success. `reachable` is False only for a genuine
    connection/timeout failure — a 401/403/500 still means the endpoint answered."""
    if not frappe.db.exists("Crema Provider", provider):
        return [], "No such provider", True

    doc = frappe.get_doc("Crema Provider", provider)
    base_url = (doc.base_url or "").rstrip("/")
    if not base_url:
        return [], "No Base URL configured", True

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
        return ids, None, True
    except (requests.ConnectionError, requests.Timeout) as exc:
        return [], _log.redact(f"{type(exc).__name__}: {exc}"), False
    except Exception as exc:
        return [], _log.redact(f"{type(exc).__name__}: {exc}"), True


@redis_cache(ttl=3600)
def list_models(provider: str) -> list[str]:
    """Model ids available from `provider`. [] on any error."""
    ids, _error, _reachable = _fetch_models(provider)
    return ids


def check_connection(provider: str) -> dict[str, Any]:
    """Live (uncached — an hour-stale status is worse than none) connectivity check
    for the Providers panel on Crema Settings. {"ok": bool, "reachable": bool,
    "detail": str}: detail is "<n> models" on success, else the redacted error
    _fetch_models returned. `reachable=False` means the endpoint itself couldn't be
    reached (DNS/connect/timeout) — as opposed to reachable but rejecting (401/403)
    or erroring (5xx)."""
    ids, error, reachable = _fetch_models(provider)
    if error is None:
        return {"ok": True, "reachable": True, "detail": f"{len(ids)} models"}
    return {"ok": False, "reachable": reachable, "detail": error}
