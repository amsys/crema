"""Frappe-metadata term harvesting — the exact-name source crema.mask cannot provide
on its own (EXPERIMENTAL, see crema/mask.py's module docstring).

In a frappe app the names worth masking are not a guess: they are records. This module
reads a document's own title and the titles of everything it links to, so those exact
strings can be masked anywhere they appear in a prompt — including inside an OCR'd
attachment that happens to name the same customer, which no per-field redaction could
reach.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import frappe


@dataclass
class TermGroup:
    """One record's surface forms — all masked under the same entity by mask.Vault.
    Duck-typed by mask.Vault (.values / .label), so mask.py itself stays frappe-free.
    """

    values: list[str] = field(default_factory=list)
    label: str = "NAME"


def _title_value(doctype: str, name: str) -> str | None:
    meta = frappe.get_meta(doctype)
    title_field = meta.get("title_field") or "name"
    if title_field == "name":
        return None  # the ID itself isn't a separate surface form worth masking
    value = frappe.db.get_value(doctype, name, title_field)
    return str(value) if value else None


def harvest(doctype: str, name: str) -> list[TermGroup]:
    """Every record-backed term `doctype`/`name` should have masked, grouped by
    record: this document's own title, and every Link field's target title, plus any
    Phone/Email field's raw value.

    Call inside sandbox.isolation(...) — this reads with frappe.get_doc/
    frappe.db.get_value under the caller's ambient permissions, so a value the
    isolation user cannot read never enters the term list. A term list is itself a
    disclosure channel: it tells the caller's chosen provider "this string means
    something", even wrapped in a token.

    Not every Data field — masking the whole document leaves the model nothing to
    work with (see crema.api._source_fields's identical reasoning for automation).
    """
    try:
        doc = frappe.get_doc(doctype, name)
    except Exception:
        return []

    groups: list[TermGroup] = []
    own_title = _title_value(doctype, name)
    if own_title:
        groups.append(TermGroup([own_title], "NAME"))

    for df in frappe.get_meta(doctype).fields:
        value = doc.get(df.fieldname)
        if not value:
            continue
        if df.fieldtype == "Link" and df.options:
            values = [str(value)]
            title = _title_value(df.options, value)
            if title and title != value:
                values.append(title)
            groups.append(TermGroup(values, "NAME"))
        elif df.fieldtype == "Phone":
            groups.append(TermGroup([str(value)], "PHONE"))
        elif df.fieldtype == "Data" and df.options == "Email":
            # Frappe has no native "Email" fieldtype (unlike "Phone", which is a real
            # one) — an email field is a plain Data field with options="Email" doing
            # the format validation. Confirmed against this bench's actual DocField
            # Select options list, not assumed.
            groups.append(TermGroup([str(value)], "EMAIL"))

    return groups
