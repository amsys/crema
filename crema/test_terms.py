"""Integration tests for crema.terms.harvest — the frappe-metadata term source
EXPERIMENTAL masking uses for exact-name detection (see crema/terms.py, crema/mask.py).

Runs against a throwaway doctype created in setUpClass, rather than guessing at the
field shape of a core frappe doctype (Contact/User's exact fieldtypes shift across
frappe versions) — self-contained, and exercises exactly the mechanism harvest() reads:
title_field, a Link field's target title, and Phone/Email fieldtype fields.
"""

from __future__ import annotations

import frappe
from crema import terms
from crema.test_fixtures import _CREATED, CremaFixtureTestCase

_LINK_DOCTYPE = "Crema Test Mask Term Company"
_MAIN_DOCTYPE = "Crema Test Mask Term Contact"


def _make_doctype(name: str, fields: list[dict]) -> None:
    if frappe.db.exists("DocType", name):
        return
    doc = frappe.get_doc(
        {
            "doctype": "DocType",
            "name": name,
            "module": "Crema",
            "custom": 1,
            "autoname": "hash",
            "fields": fields,
            "permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1}],
        }
    )
    doc.insert(ignore_permissions=True)
    # Registered with the shared fixture tracker rather than a hand-rolled
    # tearDownClass: cleanup_fixtures() drops these even when a later setUpClass step
    # fails, and the per-test savepoint keeps their tables empty by then.
    _CREATED.append(("DocType", name))


class IntegrationTestCremaTermsHarvest(CremaFixtureTestCase):
    """crema.terms.harvest() against a throwaway doctype pair: a Link target with a
    title_field, and a document linking to it plus carrying Phone/Email fields."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        frappe.set_user("Administrator")
        _make_doctype(
            _LINK_DOCTYPE,
            [{"fieldname": "company_name", "fieldtype": "Data", "label": "Company Name"}],
        )
        frappe.get_doc("DocType", _LINK_DOCTYPE).db_set("title_field", "company_name")
        frappe.clear_cache(doctype=_LINK_DOCTYPE)

        _make_doctype(
            _MAIN_DOCTYPE,
            [
                {"fieldname": "full_name", "fieldtype": "Data", "label": "Full Name"},
                {"fieldname": "phone", "fieldtype": "Phone", "label": "Phone"},
                {"fieldname": "email", "fieldtype": "Data", "options": "Email", "label": "Email"},
                {"fieldname": "password", "fieldtype": "Password", "label": "Secret"},
                {"fieldname": "company", "fieldtype": "Link", "label": "Company", "options": _LINK_DOCTYPE},
            ],
        )
        frappe.get_doc("DocType", _MAIN_DOCTYPE).db_set("title_field", "full_name")
        frappe.clear_cache(doctype=_MAIN_DOCTYPE)
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — doctype must outlive this transaction

    def _make_company(self, company_name: str) -> str:
        doc = frappe.get_doc({"doctype": _LINK_DOCTYPE, "company_name": company_name})
        doc.insert(ignore_permissions=True)
        return doc.name

    def _make_contact(self, **fields) -> str:
        doc = frappe.get_doc({"doctype": _MAIN_DOCTYPE, **fields})
        doc.insert(ignore_permissions=True)
        return doc.name

    def test_harvest_returns_the_documents_own_title(self):
        name = self._make_contact(full_name="Jean-Claude Ramgoolam")
        groups = terms.harvest(_MAIN_DOCTYPE, name)
        values = [v for g in groups for v in g.values]
        self.assertIn("Jean-Claude Ramgoolam", values)

    def test_harvest_groups_a_link_records_id_and_title_together(self):
        company_name = self._make_company("Acme Ltd")
        name = self._make_contact(full_name="Jean-Claude Ramgoolam", company=company_name)
        groups = terms.harvest(_MAIN_DOCTYPE, name)
        company_group = next(g for g in groups if "Acme Ltd" in g.values)
        self.assertIn(company_name, company_group.values)

    def test_harvest_labels_phone_and_email_fields_separately(self):
        name = self._make_contact(
            full_name="Jean-Claude Ramgoolam", phone="+230 5251 4412", email="jc@acme.mu"
        )
        groups = terms.harvest(_MAIN_DOCTYPE, name)
        by_label = {g.label: [v for v in g.values] for g in groups}
        self.assertIn("+230 5251 4412", by_label.get("PHONE", []))
        self.assertIn("jc@acme.mu", by_label.get("EMAIL", []))

    def test_harvest_skips_password_fieldtype_values(self):
        name = self._make_contact(full_name="Jean-Claude Ramgoolam", password="s3cr3t")
        groups = terms.harvest(_MAIN_DOCTYPE, name)
        values = [v for g in groups for v in g.values]
        self.assertNotIn("s3cr3t", values)

    def test_harvest_returns_empty_list_for_a_missing_record(self):
        self.assertEqual(terms.harvest(_MAIN_DOCTYPE, "does-not-exist"), [])
