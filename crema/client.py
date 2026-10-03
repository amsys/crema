"""litellm client boundary. A provider's api_key is read in one function only,
_api_key, and used only in this module.

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
from frappe import _
from frappe.utils import cint, now_datetime
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
        "per_user_budget_usd": settings.get("default_monthly_budget_usd_per_user") or 0,
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
        raise CremaConfigError(
            _(
                "Crema is disabled. A System Manager can re-enable it in Crema Settings, "
                "unless the site configuration disabled it."
            )
        )

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
            # Stamped here, before any caller opens sandbox.isolation(cfg
            # ["isolation_user"]) — frappe.session.user read after that point is the
            # isolation user, not the real caller (see api.py's isolation blocks).
            # Never cached: _resolve_one's redis cache is keyed by interface alone,
            # and a cached caller_user would leak onto a later, different user's call.
            return {**cfg, "requested": interface, "caller_user": frappe.session.user}

        seen.add(name)
        fallback = interfaces.fallback_for(name)
        if fallback is None or fallback in seen:
            raise CremaConfigError(f"No usable configuration found for crema interface '{interface}'")
        name = fallback


def _api_key(provider: str, *, refresh: bool = False) -> str | None:
    """Decrypted provider api_key, memoized request-local only (never redis). The only
    place crema reads the key. The memo is also the list log.redact strips from every
    error text, so a provider that echoes its key back cannot leak it. `refresh` reads
    the key again: the uncached connection check must see a key saved a moment ago."""
    if not hasattr(frappe.local, "crema_keys"):
        frappe.local.crema_keys = {}

    if refresh or provider not in frappe.local.crema_keys:
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


def _proxy_cost(hidden: dict[str, Any]) -> float | None:
    """The cost a litellm-proxy in front of this call reported, or None when there
    is none to read. Behind a proxy, litellm's own cost map is keyed by the SDK's
    model name — a proxy-served custom model name resolves to 0 there, silently
    breaking every budget. The proxy's own response carries the real figure in an
    `x-litellm-response-cost` header; litellm's process_response_headers treats a
    raw upstream header as untrusted and prefixes it `llm_provider-`, so check both
    forms.

    A real header value is always a string — the explicit isinstance check (not a
    bare float() coercion) is what rejects a MagicMock test double the same way
    _as_number does: MagicMock configures __float__ with a harmless-looking
    non-zero default rather than raising, so float(a_magicmock) "succeeds" and
    would silently invent a cost."""
    headers = hidden.get("additional_headers")
    if not isinstance(headers, dict):
        return None
    raw = headers.get("llm_provider-x-litellm-response-cost") or headers.get("x-litellm-response-cost")
    if not isinstance(raw, str):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


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
            "served_model": None,
        }

    usage = getattr(response, "usage", None)
    hidden = getattr(response, "_hidden_params", None) or {}
    proxy_cost = _proxy_cost(hidden)

    acc = frappe.local.crema_usage
    acc["llm_calls"] += 1
    acc["prompt_tokens"] += int(_as_number(getattr(usage, "prompt_tokens", 0)))
    acc["completion_tokens"] += int(_as_number(getattr(usage, "completion_tokens", 0)))
    acc["cost_usd"] += proxy_cost if proxy_cost is not None else _as_number(hidden.get("response_cost"))

    # The isinstance check, not a bare truthy getattr, is load-bearing for the same
    # reason as _as_number/_proxy_cost above: a bare MagicMock test double auto-vivifies
    # response.model into another MagicMock rather than raising or returning None. Last
    # write wins on purpose — a fallback chain or _ocr's escalation can log several calls
    # under one row, and the served model that matters is the one behind the answer
    # actually returned.
    served = getattr(response, "model", None)
    if isinstance(served, str) and served:
        acc["served_model"] = served


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
    passes through the guardrails onion (crema.guardrails.run): masking and the
    reply check wrap _call_raw here, in the row order the Guardrails doctype sets.
    Integration tests mock exactly this boundary."""
    from crema import guardrails

    return guardrails.run(cfg, messages, lambda msgs: _call_raw(cfg, msgs, response_format), response_format)


def _call_raw(cfg: dict[str, Any], messages: list[dict], response_format: dict | None = None) -> str:
    """The bare litellm.completion call. No guardrail runs here; only _complete's own
    closure calls it."""
    import litellm

    kwargs: dict[str, Any] = {
        "model": f"openai/{cfg['model']}",
        "messages": messages,
        "api_base": cfg["base_url"],
        "api_key": _api_key(cfg["provider"]),
        "timeout": cfg["timeout_seconds"],
        "temperature": cfg["temperature"],
        "num_retries": 1,
        "user": cfg["caller_user"],
        "metadata": {"tags": [cfg["interface"]]},
    }
    if response_format:
        kwargs["response_format"] = response_format
    if cfg.get("max_tokens"):
        kwargs["max_tokens"] = cfg["max_tokens"]

    response = litellm.completion(**kwargs)
    _record_usage(response)
    # A reply with only a tool call or a refusal can carry no text at all.
    content = response.choices[0].message.content or ""
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
        "user": cfg["caller_user"],
        "metadata": {"tags": [cfg["interface"]]},
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


def _fetch_models(provider: str) -> tuple[list[str], str | None, bool, str]:
    """(ids, error, reachable, kind). Shared by list_models (cached, ids only) and
    check_connection (uncached, surfaces the error) — litellm has no generic
    list-models call, so this hits the OpenAI-compatible GET {base_url}/models
    directly. Ids that don't match _MODEL_ID_RE are silently dropped from the ids
    list but don't affect success. `reachable` is False only for a genuine
    connection/timeout failure — a 401/403/500 still means the endpoint answered.
    `kind` names the outcome for the hourly sweep (see check_providers): "ok",
    "unreachable", "server_error", "auth", "rate_limited", "unsupported" or "config"."""
    if not frappe.db.exists("Crema Provider", provider):
        return [], "No such provider", True, "config"

    doc = frappe.get_doc("Crema Provider", provider)
    base_url = (doc.base_url or "").rstrip("/")
    if not base_url:
        return [], "No Base URL configured", True, "config"

    api_key = _api_key(provider, refresh=True)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    try:
        response = requests.get(f"{base_url}/models", headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json().get("data", [])
        ids = [
            m["id"]
            for m in data
            if isinstance(m, dict) and isinstance(m.get("id"), str) and _MODEL_ID_RE.match(m["id"])
        ]
        return ids, None, True, "ok"
    except (requests.ConnectionError, requests.Timeout) as exc:
        return [], _log.redact(f"{type(exc).__name__}: {exc}"), False, "unreachable"
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return [], _log.redact(f"{type(exc).__name__}: {exc}"), True, _failure_kind(status)


def _failure_kind(status: int | None) -> str:
    """The sweep's name for an answered but failed /models request."""
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limited"
    if status is not None and status >= 500:
        return "server_error"
    # 404/405 (a gateway with no /models), another 4xx, or a body that is not the
    # expected JSON: the check cannot judge this provider.
    return "unsupported"


@redis_cache(ttl=3600)
def list_models(provider: str) -> list[str]:
    """Model ids available from `provider`. [] on any error."""
    ids, _error, _reachable, _kind = _fetch_models(provider)
    return ids


def check_connection(provider: str) -> dict[str, Any]:
    """Live (uncached — an hour-stale status is worse than none) connectivity check
    for the Providers panel on Crema Settings. {"ok": bool, "reachable": bool,
    "detail": str, "kind": str}: detail is "<n> models" on success, else the redacted
    error _fetch_models returned. `reachable=False` means the endpoint itself couldn't
    be reached (DNS/connect/timeout) — as opposed to reachable but rejecting (401/403)
    or erroring (5xx). `kind` is _fetch_models' name for the outcome."""
    ids, error, reachable, kind = _fetch_models(provider)
    if error is None:
        return {"ok": True, "reachable": True, "detail": f"{len(ids)} models", "kind": kind}
    if kind == "unsupported":
        error = f"This service does not support the health check. {error}"
    return {"ok": False, "reachable": reachable, "detail": error, "kind": kind}


# How many failed hourly checks in a row switch a provider off, per kind of failure.
# A rejected key fails every call, so one check is enough; a network or server fault
# is often brief, so the sweep waits three hours. Any other kind never switches a
# provider off: a rate limit means it works, a gateway without /models still serves
# completions, and a configuration error was never usable to begin with.
_FAILURE_LIMITS = {"auth": 1, "unreachable": 3, "server_error": 3}


def check_providers() -> None:
    """Scheduler entry point (hourly — same cadence as list_models' cache): the
    scheduled half of automatic fallback. Runs only when Crema Settings' "Switch Off
    Services That Fail" is on and the kill switch is off.

    For each provider, the check result decides (see _FAILURE_LIMITS):
    - a passing check resets the failure count and switches back on a provider this
      sweep switched off (`auto_disabled`); a provider an admin switched off stays off;
    - a failure counts towards its limit, and at the limit the provider goes off,
      unless it is the last enabled provider — switching that one off only changes
      the error every call gets;
    - System Managers get one notification when a provider goes off, comes back, or
      reaches its limit as the last enabled provider.
    """
    if policy.disabled():
        return
    if not cint(frappe.get_cached_doc("Crema Settings").get("auto_disable_unreachable")):
        return

    providers = frappe.get_all(
        "Crema Provider", fields=["name", "enabled", "auto_disabled", "check_failures"]
    )
    enabled_count = sum(1 for p in providers if p.enabled)
    flipped = False
    for provider in providers:
        try:
            status = check_connection(provider.name)
        except Exception as exc:
            frappe.log_error(
                title=f"Crema provider health check failed: {provider.name}"[:140],
                message=_log.redact(str(exc)),
            )
            continue

        values: dict[str, Any] = {"last_checked": now_datetime(), "last_check_detail": status["detail"]}
        limit = _FAILURE_LIMITS.get(status["kind"])
        failures = cint(provider.check_failures) + 1 if limit else 0
        values["check_failures"] = failures

        if status["ok"] and not provider.enabled and provider.auto_disabled:
            values.update(enabled=1, auto_disabled=0)
            enabled_count += 1
            flipped = True
            _notify(provider.name, _("Crema switched the AI service {0} back on.").format(provider.name))
        elif limit and provider.enabled and failures >= limit:
            if enabled_count > 1:
                values.update(enabled=0, auto_disabled=1)
                enabled_count -= 1
                flipped = True
                _notify(
                    provider.name,
                    _("Crema switched off the AI service {0}: {1}").format(provider.name, status["detail"]),
                )
            elif failures == limit:
                _notify(
                    provider.name,
                    _("The AI service {0} fails its check, but it is the last one switched on: {1}").format(
                        provider.name, status["detail"]
                    ),
                )
        frappe.db.set_value("Crema Provider", provider.name, values, update_modified=False)

    if flipped:
        # Load-bearing, not a courtesy: _resolve_one's redis cache is keyed per
        # interface, so a flip that skipped this would leave a resolved config
        # pointing at a provider that just changed out from under it.
        cache.clear_provider()
    # nosemgrep: frappe-manual-commit — a scheduler job commits its own work
    frappe.db.commit()


def _notify(provider: str, subject: str) -> None:
    """One in-app notification to every System Manager about `provider`."""
    from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
    from frappe.utils.user import get_users_with_role

    enqueue_create_notification(
        get_users_with_role("System Manager"),
        {
            "type": "Alert",
            "subject": subject,
            "document_type": "Crema Provider",
            "document_name": provider,
            "from_user": "Administrator",
        },
    )
