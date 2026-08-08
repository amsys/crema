"""Crema Health Word controller.

crema.mask's built-in PHI word list (mask._PHI_TERMS) is English only, so the "Hide
Health Information" guardrail hides nothing on a site running in another language.
This doctype lets a site add its own words — by hand, or proposed by translate_words()
below, which asks crema's own "translation" interface for a target language's version
of the built-in list and files the result switched OFF, for a System Manager to review
and enable. Read by crema.guardrails._phi_patterns, cached in crema.cache until the
next save/delete here.
"""

from __future__ import annotations

import frappe
from crema import cache, mask
from crema.api import ask_json
from crema.exceptions import CremaBlockedError, CremaBudgetError, CremaConfigError
from frappe import _
from frappe.model.document import Document


class CremaHealthWord(Document):
    def on_update(self) -> None:
        cache.clear_health_words()

    def on_trash(self) -> None:
        cache.clear_health_words()


_INSTRUCTION = (
    "Translate this list of English medical and health keywords into {language}. Keep "
    "the list flat, one term per entry, same order, no explanations. "
    'Output ONLY JSON `{{"words": ["...", ...]}}`.'
)


@frappe.whitelist()
def translate_words(language: str) -> dict:
    """Data source for the "Translate Built-in List" list action
    (crema_health_word_list.js). Never applies a word itself: an over-matching term
    tokenises ordinary text, and on a Block row an unrestored token fails the whole
    call (guardrails._Mask.after) — so a bad translation must cost a review, not a
    broken request."""
    frappe.only_for("System Manager")
    language_name = frappe.db.get_value("Language", language, "language_name") or language
    instruction = _INSTRUCTION.format(language=language_name)
    try:
        # The word list travels in `context`, never concatenated into the instruction —
        # same discipline as api.transform's payload.
        raw = ask_json("translation", instruction, context=frappe.as_json(list(mask._PHI_TERMS)))
    except (CremaBlockedError, CremaConfigError, CremaBudgetError) as exc:
        frappe.throw(str(exc))
    except ValueError:  # json.JSONDecodeError: ask_json's json.loads is uncaught by design
        frappe.throw(_("The model did not return a usable word list. Try again."))

    proposed = raw.get("words") if isinstance(raw, dict) else None
    words = mask.clean_terms(proposed)
    existing = {w.casefold() for w in frappe.get_all("Crema Health Word", pluck="word")}

    added = 0
    for word in words:
        if word.casefold() in existing:
            continue
        frappe.get_doc(
            {"doctype": "Crema Health Word", "word": word, "language": language, "enabled": 0}
        ).insert()
        existing.add(word.casefold())
        added += 1
    return {"added": added, "skipped": len(words) - added}
