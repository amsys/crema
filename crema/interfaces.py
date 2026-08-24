"""Predefined interface names, fallback chain, and default system prompts.

No settings singleton, no abstract base classes — just data that client._resolve()
walks through.
"""

from __future__ import annotations

import frappe

# Interfaces that only crema itself may drive. "advanced_ocr" is an internal
# escalation target reached through ocr(), never addressed directly. Also not
# selectable for an automation task — a task should never run as it. (The old
# "security" interface is gone: it backed the AI guard, a text-only check that
# now belongs on the gateway, not in crema — see docs/security.md.)
INTERNAL = frozenset({"advanced_ocr"})

# Selectable everywhere else, but never by an automation task, which drives its profile
# through api.ask/ask_json and owns its own plan. "view" and "transform" each emit an
# output shape only their caller applies — a desk list-view spec or a proposed record
# change, and a single-document diff. "ocr" forces {"text", "confidence"}, which the
# PLAN stage cannot parse a plan out of. "transcribe" is not a chat interface at all: no
# default prompt, and its provider takes audio, not chat completions.
# Not INTERNAL: the desk UI drives "view", transform_api drives "transform", and ocr()
# and transcribe() are public functions.
NOT_FOR_TASKS = frozenset({"view", "transform", "ocr", "transcribe"})

PREDEFINED = [
    "simple",
    "translation",
    "complex",
    "ocr",
    "advanced_ocr",
    "extraction",
    "classification",
    "summarization",
    "transform",
    "view",
    "transcribe",
]

# Walked by client._resolve() until a configured interface with an enabled provider is
# found. "advanced_ocr" and "transcribe" deliberately have no fallback (see _resolve)
# — a transcription call cannot fall back to a chat model.
FALLBACKS = {
    "translation": "simple",
    "classification": "simple",
    "summarization": "simple",
    "extraction": "complex",
    "transform": "complex",
    "view": "complex",
    "complex": "simple",  # end of chain; unresolved "simple" -> CremaConfigError
}

# Human-readable labels for the Crema Settings Model Assignments grid — the stored
# interface key (passed to ask("simple", ...) etc.) never changes.
LABELS = {
    "simple": "Default",
    "translation": "Translation",
    "complex": "Complex",
    "ocr": "OCR",
    "advanced_ocr": "Advanced OCR",
    "extraction": "Extraction",
    "classification": "Classification",
    "summarization": "Summarization",
    "transform": "Transform",
    "view": "List Assistant",
    "transcribe": "Transcribe",
}

DEFAULT_PROMPTS = {
    "simple": "You are a helpful assistant. Answer concisely and accurately.",
    "translation": "You are a professional translator. Translate the given text faithfully, "
    "preserving tone and meaning. Output only the translation.",
    "complex": "You are a careful, thorough assistant for demanding tasks. Think step by step "
    "and give a complete, accurate answer.",
    "ocr": "Extract the text from the provided document. Output ONLY JSON "
    '`{"text": "...", "confidence": 0-1}`.',
    "advanced_ocr": "You are a meticulous OCR specialist handling documents that a faster model "
    'struggled with. Extract the text carefully. Output ONLY JSON `{"text": "...", "confidence": 0-1}`.',
    "extraction": "Extract structured data from the given content per the caller's instructions. "
    "Output only the requested JSON — no commentary.",
    "classification": "Classify the given content per the caller's instructions. Output only the "
    "requested label or JSON — no commentary.",
    "summarization": "Summarize the given content concisely and accurately, preserving key facts.",
    "transform": "You propose changes to an ERP document. NEVER invent fields. Output ONLY "
    '`{"set": {fieldname: new_value}, "child_set": {table_fieldname: [row dicts]}, "reason": "..."}`.',
    # Deliberately just a role statement, not the output contract — that contract
    # (the "action" discriminator: view/create/edit/delete/none) lives in the user turn
    # crema.bundle.js's crema_view_prompt builds, not here. CremaModelAssignment.validate
    # only seeds a row's system_prompt when it is empty, so an already-configured site
    # never sees an edit to this string — see patches/refresh_view_prompt.py, which
    # clears the contradiction in an already-stored prompt exactly once. A prompt that
    # never carries the contract never goes stale, and never needs a patch again.
    "view": "You turn a request about a list of ERP records into a JSON specification "
    "the desk applies. NEVER invent fieldnames — only use ones given in the schema. "
    "Follow the output contract in the user message exactly, and output ONLY that JSON.",
}


def app_interfaces() -> dict[str, dict]:
    """Interfaces contributed by installed apps via the `crema_interfaces` hooks.py key
    — {name: {"prompt": str, "fallback": str | None, "label": str | None, ...any other
    Crema Model Assignment fieldname}}. "prompt"/"fallback"/"label" are read separately
    (prompt_for/fallback_for/label_for) rather than seeded onto the row — none of the
    three is itself a Crema Model Assignment fieldname. The hooks.py value can be a
    literal dict, or a dotted path
    string to one (resolved lazily via frappe.get_attr) — the same convention Frappe's
    own hooks already use for after_install/scheduler_events/doc_events, so an app
    whose config dict sits behind a module of real prompt text isn't forced to import
    that module on every process boot, only the first time a caller actually asks
    something. Read app-by-app via a plain module import, not frappe.get_hooks: that
    helper's dict-merge (frappe.append_hook) wraps every leaf value in a list, which is
    right for hooks that register repeated function paths (doc_events) and wrong for a
    single static config dict per app. First app wins a name collision; a name already
    in PREDEFINED is dropped so an app can never redefine a core interface."""
    merged: dict[str, dict] = {}
    for app in frappe.get_installed_apps():
        try:
            hooks_module = frappe.get_module(f"{app}.hooks")
        except ImportError:
            continue
        declared = getattr(hooks_module, "crema_interfaces", None)
        if not declared:
            continue
        try:
            cfg = frappe.get_attr(declared) if isinstance(declared, str) else declared
        except Exception:
            # Keep going — a bad third-party hook must not break boot — but say so, or a
            # typo'd dotted path costs its author a silently missing interface and no
            # trace. frappe.logger, not log_error: names() calls this on every resolve
            # with no memoization, and a row per call would flood the Error Log.
            frappe.logger("crema").warning(
                f"{app}: crema_interfaces path '{declared}' did not resolve — interfaces skipped"
            )
            continue
        for name, row in (cfg or {}).items():
            if name not in PREDEFINED:
                merged.setdefault(name, row)
    return merged


def names() -> list[str]:
    """PREDEFINED, then every app-registered name — the full set
    CremaSettings._reconcile_assignments keeps one row per."""
    return PREDEFINED + list(app_interfaces())


def prompt_for(name: str) -> str:
    """DEFAULT_PROMPTS for a core interface, else the app's own "prompt", else ""."""
    if name in DEFAULT_PROMPTS:
        return DEFAULT_PROMPTS[name]
    return app_interfaces().get(name, {}).get("prompt", "")


def fallback_for(name: str) -> str | None:
    """FALLBACKS for a core interface, else the app's own "fallback", else None."""
    if name in FALLBACKS:
        return FALLBACKS[name]
    return app_interfaces().get(name, {}).get("fallback")


def label_for(name: str) -> str:
    """LABELS for a core interface, else the app's own "label", else the raw name —
    used everywhere a human reads an interface name (the Crema Settings grid, a Crema
    Log row's title)."""
    return LABELS.get(name) or app_interfaces().get(name, {}).get("label") or name


def selectable() -> list[str]:
    """Interfaces an automation task may be assigned to: names() minus INTERNAL (the
    OCR escalation target) and minus NOT_FOR_TASKS (the ones whose output shape only
    their own caller can apply). This is more than a picker narrowing:
    install.sync_interface_options() stamps this list onto the "interface" field's Select
    options via a Property Setter, and frappe validates Select options server-side on
    every save — stricter than CremaAutomationTask.validate's own names() membership
    check. A task can no longer be saved with any excluded interface at all."""
    excluded = INTERNAL | NOT_FOR_TASKS
    return [name for name in names() if name not in excluded]
