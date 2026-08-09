"""Crema Log insert helper — shared by crema.api and crema._ocr.

Kept in its own module (rather than crema.api, where it originated) because crema._ocr is
imported BY crema.api (`from crema import _ocr as _ocr_impl`); if _ocr needed crema.api's
_log helper directly, that would be a circular import. Both modules import this one
instead, and neither imports the other.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from crema import interfaces
from crema.exceptions import CremaBudgetError

# Per side of one round trip. An OCR payload or a long extraction can be far larger than
# anything worth reading in a timeline comment.
_TRANSCRIPT_CHARS = 20000


def redact(text: str) -> str:
    """Strip every provider api_key touched in this request out of `text`.

    `frappe.local.crema_keys` is client._api_key's request-local memo — exactly the set
    of keys that could plausibly have ended up inside an exception message on this
    code path. Defensive only: no provider embeds its key in exception text today,
    but this text lands in Crema Log.detail, Crema Automation Task.last_error and
    frappe.log_error, so an SDK change must not turn those into a key leak.
    """
    for key in (getattr(frappe.local, "crema_keys", None) or {}).values():
        if key:
            text = text.replace(key, "***")
    return text


def _drain_usage() -> dict[str, Any]:
    """Pop and reset the request-local usage accumulator client._record_usage fills.

    Draining (not just reading) keeps attribution per logged row: everything billed
    since the last row — including the AI guard's own call, which runs inside the
    caller's pipeline and logs no row of its own — lands on the row that drains it,
    so a guarded call shows llm_calls=2 with both calls' tokens and cost. A call that
    never reached the provider (Blocked, a cache hit) drains an accumulator nothing
    added to, i.e. all zeros.
    """
    acc = getattr(frappe.local, "crema_usage", None)
    frappe.local.crema_usage = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
    if not acc:
        return {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
    return acc


def _drain_trap() -> str | None:
    """Pop and reset the request-local layer-3 (output trap) miss reason
    client._record_trap_miss sets on a non-blocking ("Log Only") miss. Same
    drain-on-every-call pattern as _drain_usage, so a stray reason from one call never
    leaks onto the next call's log row."""
    reason = getattr(frappe.local, "crema_trap", None)
    frappe.local.crema_trap = None
    return reason


def _drain_mask() -> str | None:
    """Pop and reset the request-local EXPERIMENTAL masking miss reason
    crema.guardrails._record_mask_miss sets on a non-blocking ("Log Only") unresolved
    token or unmaskable content part. Same drain-on-every-call pattern as _drain_trap."""
    reason = getattr(frappe.local, "crema_mask", None)
    frappe.local.crema_mask = None
    return reason


def _drain_note() -> str | None:
    """Pop and reset the request-local guardrail note crema.guardrails._record_note
    sets (a Log Only scan hit, the AI guard's fail-open or "suspicious" verdict). Same
    drain-on-every-call pattern as _drain_trap/_drain_mask."""
    reason = getattr(frappe.local, "crema_note", None)
    frappe.local.crema_note = None
    return reason


def _drain_transcript() -> list[dict]:
    """Pop and reset client._record_transcript's developer_mode-only accumulator. Same
    drain-on-every-call contract as _drain_usage/_drain_trap, so a round trip recorded
    for one logged row can never trail onto the next one."""
    calls = getattr(frappe.local, "crema_transcript", None)
    frappe.local.crema_transcript = None
    return calls or []


def _attach_transcript(doc: Any, calls: list[dict]) -> None:
    """Write the provider round trips of one logged call into the Crema Log row's
    timeline, as an ordinary comment.

    A comment, not a field: Crema Log's contract is that no field of it can hold prompt
    or document text (pinned by test_doctypes.test_no_field_can_hold_prompt_or_document_text),
    and that stays true. Anyone who can read the row can read the comment, which is
    acceptable only because client._record_transcript records nothing at all unless the
    site is in developer mode.
    """
    from html import escape

    blocks = []
    for index, call in enumerate(calls, start=1):
        sent = json.dumps(call.get("messages"), indent=2, default=str)
        blocks.append(
            f"<b>Call {index} — sent</b><pre>{escape(redact(sent)[:_TRANSCRIPT_CHARS])}</pre>"
            f"<b>Call {index} — received</b>"
            f"<pre>{escape(redact(str(call.get('response') or ''))[:_TRANSCRIPT_CHARS])}</pre>"
        )
    doc.add_comment("Info", "".join(blocks))


def insert(
    interface: str | None,
    model: str | None,
    status: str,
    detail: str | None,
    prompt_sha: str | None = None,
    duration_ms: int | None = None,
    provider: str | None = None,
) -> None:
    """Insert a Crema Log row. Never stores prompt/document content — only correlation data.

    Token/cost/call-count fields come from crema.client._record_usage's request-local
    accumulator, drained here — callers never pass usage explicitly. A "Log Only"
    reply-check miss, a "Log Only" masking miss, and any other guardrail note (see
    crema.guardrails._record_note) are folded into `detail` the same way, but only on
    a Success row — a Blocked/Error row already carries its own more specific detail
    string.

    The one exception to "never stores prompt content" is a developer_mode site, where
    the round trips client._record_transcript captured are attached to the row as a
    comment — see _attach_transcript. Off, and empty, on any other site.
    """
    usage = _drain_usage()
    trap_reason = _drain_trap()
    mask_reason = _drain_mask()
    note_reason = _drain_note()
    transcript = _drain_transcript()
    if status == "Success":
        for reason in (trap_reason, mask_reason, note_reason):
            if reason:
                detail = reason if not detail else f"{detail}; {reason}"
    try:
        doc = frappe.get_doc(
            {
                "doctype": "Crema Log",
                "interface": interface,
                "interface_label": interfaces.label_for(interface) if interface else None,
                "model": model,
                "provider": provider,
                "user": frappe.session.user,
                "status": status,
                "detail": detail,
                "prompt_sha": prompt_sha,
                "duration_ms": duration_ms,
                "prompt_tokens": usage["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"],
                "total_tokens": usage["prompt_tokens"] + usage["completion_tokens"],
                "cost_usd": usage["cost_usd"],
                "llm_calls": usage["llm_calls"],
            }
        )
        doc.insert(ignore_permissions=True)
        if transcript:
            _attach_transcript(doc, transcript)
    except Exception:
        frappe.log_error(title="Crema Log insert failed")


def verify_chain() -> dict[str, Any]:
    """Walk every Crema Log row in true insertion order and recompute its chain_sha
    from its predecessor, confirming no row's audit fields — or its very presence —
    changed since it was written. `bench execute crema.log.verify_chain`.

    Ordered by chain_seq, not creation — see CremaLog._tail_chain_state's own comment
    for why creation can't be trusted to break a tie between rows written close
    together. A row with no chain_seq at all predates this feature; it sorts first
    (nothing chained yet ever produced one) and is skipped, resetting the chain for
    whatever comes after it, the same way a row missing entirely would.

    Anchors on the oldest surviving chained row's own stored chain_sha rather than an
    empty string: `automation.cleanup_logs` deletes a prefix of the table (rows older
    than Crema Settings.log_retention_days), so the oldest surviving row's real
    predecessor is usually already gone — a break there is undetectable, by
    construction, and this does not try to report one.

    Returns {"checked": <rows actually verified>, "ok": <bool>,
    "first_break": <name of the first row whose stored and recomputed hashes disagree,
    or None>}.
    """
    from crema.crema.doctype.crema_log.crema_log import CHAIN_FIELDS, chain_hash

    # fields is built from CHAIN_FIELDS, a fixed module-level constant -- no user input
    # reaches this query. chain_seq is one of CHAIN_FIELDS, so it's already included.
    fields = ", ".join(["name", "chain_sha", *CHAIN_FIELDS])
    rows = frappe.db.sql(  # nosemgrep: frappe-sql-format-injection
        f"select {fields} from `tabCrema Log` order by chain_seq asc", as_dict=True
    )

    checked = 0
    previous: str | None = None
    for row in rows:
        if not row.chain_sha:
            previous = None
            continue
        if previous is None:
            # The anchor row (see the docstring): its predecessor is legitimately gone
            # (retention cleanup) or never chained (a pre-feature row), so its own hash
            # cannot be recomputed — trust its stored chain_sha and verify from here on.
            previous = row.chain_sha
            continue
        checked += 1
        if chain_hash(row, previous) != row.chain_sha:
            return {"checked": checked, "ok": False, "first_break": row.name}
        previous = row.chain_sha
    return {"checked": checked, "ok": True, "first_break": None}


def month_spend() -> dict[str, dict[str, float]]:
    """Every interface's, every provider's, and every user's total cost_usd since the
    start of the current calendar month, in one query — used by check_budget (all three
    ceilings) and api.get_usage (the Crema Settings grid's Usage column)."""
    from frappe.query_builder.functions import Sum
    from frappe.utils import get_first_day, now_datetime

    log = frappe.qb.DocType("Crema Log")
    rows = (
        frappe.qb.from_(log)
        .select(log.interface, log.provider, log.user, Sum(log.cost_usd).as_("cost_usd"))
        .where(log.creation >= get_first_day(now_datetime()))
        .groupby(log.interface, log.provider, log.user)
        .run(as_dict=True)
    )
    by_interface: dict[str, float] = {}
    by_provider: dict[str, float] = {}
    by_user: dict[str, float] = {}
    for row in rows:
        cost = float(row.cost_usd or 0)
        if row.interface:
            by_interface[row.interface] = by_interface.get(row.interface, 0) + cost
        if row.provider:
            by_provider[row.provider] = by_provider.get(row.provider, 0) + cost
        if row.user:
            by_user[row.user] = by_user.get(row.user, 0) + cost
    return {"interface": by_interface, "provider": by_provider, "user": by_user}


def check_budget(cfg: dict[str, Any]) -> None:
    """Raise CremaBudgetError if `cfg`'s interface, provider, or calling user has
    already spent its monthly budget this calendar month. 0 (the default) means unlimited.

    Per-user: the metered identity is frappe.session.user — the same identity
    log.insert records. Skipped for Administrator (a currency ceiling that blocks
    bench execute is a debugging trap; the interface and provider ceilings still cap
    the spend) and for automation runs (where the session user is the isolation user,
    pooling every task into one meaningless ceiling).

    Called after the cache-hit branch (a cache hit spends nothing and must not be
    blocked) and before the provider is ever called. Note: after a fallback walk,
    `cfg["interface"]` is the fallback's name — a blocked call is charged to, and
    logged against, the interface that actually serves it, consistent with how
    logging already attributes fallback-served calls.
    """
    spend = month_spend()

    interface_budget = cfg.get("monthly_budget_usd") or 0
    if interface_budget and spend["interface"].get(cfg["interface"], 0) >= interface_budget:
        raise CremaBudgetError(f"'{cfg['interface']}': monthly budget of ${interface_budget} already spent.")

    provider_budget = cfg.get("provider_budget_usd") or 0
    if provider_budget and spend["provider"].get(cfg.get("provider"), 0) >= provider_budget:
        raise CremaBudgetError(
            f"Provider '{cfg.get('provider')}': monthly budget of ${provider_budget} already spent."
        )

    per_user_budget = cfg.get("per_user_budget_usd") or 0
    if (
        per_user_budget
        and frappe.session.user != "Administrator"
        and not getattr(frappe.local, "crema_in_automation", False)
    ):
        user_spend = spend["user"].get(frappe.session.user, 0)
        if user_spend >= per_user_budget:
            raise CremaBudgetError(
                f"User '{frappe.session.user}': monthly budget of ${per_user_budget} already spent."
            )
