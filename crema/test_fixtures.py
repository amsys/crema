"""Shared fixtures for the crema test suite — no tests of its own.

Frappe's loader globs `test_*.py` (frappe/testing/loader.py), so this module is
imported like any other test module and simply yields no test methods. The name has to
match that glob to stay consistent with the rest of the suite, which sits beside the
code it tests rather than in a separate `tests/` package (see CLAUDE.md).

Everything here used to live in `test_client.py`, which meant every other test module
imported the client tests to get a user fixture — and several had to do it from inside
a function to dodge an import cycle. This module imports nothing from the suite, so
those cycles cannot exist.
"""

from __future__ import annotations

import io

import pymupdf
from PIL import Image as PILImage

import frappe
from crema import cache, interfaces, testing
from frappe.tests import IntegrationTestCase

# Defined in crema.testing, the supported bootstrap a consuming app's own test suite
# imports — re-exported under these names here so the rest of this module (and every
# test_*.py that imports them) didn't need to change.
TEST_PROVIDER = testing.DEFAULT_PROVIDER
TEST_ISOLATION_USER = testing.DEFAULT_ISOLATION_USER
TEST_SANDBOX_USER = "_test_crema_sandbox_user@example.com"
TEST_SANDBOX_VICTIM = "_test_crema_sandbox_victim@example.com"
TEST_PLAIN_USER = "_test_crema_plain_user@example.com"

# ---------------------------------------------------------------------------
# fixture tracking — undoes what setUpClass commits outside the per-test rollback
# ---------------------------------------------------------------------------
#
# IntegrationTestCase rolls back each *test*, but every setUpClass here calls
# frappe.db.commit() (isolation() needs the row visible in a real transaction, not
# just the test's uncommitted one). That commit survives past the test class, so
# repeated suite runs leave rows behind on whatever site the suite is pointed at —
# see CLAUDE.md's `bench --site fcr.local run-tests --app crema`. Track exactly what
# each helper touches and undo it in CremaFixtureTestCase.tearDownClass.

_CREATED: list[tuple[str, str]] = []
_MODIFIED: list[tuple[str, str, dict]] = []
_ROLES_GRANTED: list[tuple[str, list[str]]] = []


def cleanup_fixtures() -> None:
    for user, roles in reversed(_ROLES_GRANTED):
        if frappe.db.exists("User", user):
            frappe.get_doc("User", user).remove_roles(*roles)
    _ROLES_GRANTED.clear()

    for doctype, name, old_values in reversed(_MODIFIED):
        if frappe.db.exists(doctype, name):
            frappe.db.set_value(doctype, name, old_values, update_modified=False)
    _MODIFIED.clear()

    for doctype, name in reversed(_CREATED):
        if frappe.db.exists(doctype, name):
            frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)
    _CREATED.clear()

    # _MODIFIED's frappe.db.set_value calls above touch "Crema Model Assignment" child
    # rows directly, which invalidates only that child doctype's own cache
    # (frappe.database.set_value clears by dt+dn) — NOT the "Crema Settings" parent
    # Single's document cache, and not crema's own crema:iface: redis cache either.
    # Both must be cleared explicitly or a later test reads stale assignment data.
    # Same for the Crema Guardrails Single, whose child rows _set_guardrail modifies.
    frappe.clear_document_cache("Crema Settings")
    frappe.clear_document_cache("Crema Guardrails")
    cache.clear_interfaces()

    frappe.db.commit()  # nosemgrep: frappe-manual-commit — fixture must outlive this transaction


class CremaFixtureTestCase(IntegrationTestCase):
    """Base for classes whose setUpClass calls _ensure_user/_ensure_provider/
    _ensure_interface (or inserts a fixture doc directly) and commits it.

    setUpClass's frappe.db.commit() ends that transaction for real, so undoing it in
    tearDownClass needs a real commit too -- frappe.db.rollback() can't touch data an
    earlier commit already made permanent. But a commit there would also permanently
    persist anything any test method in the class wrote and never explicitly deleted
    (there is no other per-test rollback in this frappe version — see
    IntegrationTestCase.setUpClass: the only rollback it registers is one
    addClassCleanup(_rollback_db) for the whole class). Wrap every test method in its
    own savepoint so its writes never survive past its own tearDown, leaving
    tearDownClass's commit to persist only what cleanup_fixtures() itself restores.
    """

    _SAVEPOINT = "crema_fixture_test"

    @classmethod
    def setUpClass(cls) -> None:
        """Every fixture class starts from the guardrails baseline (scan on, all else
        off) — not just the ones that go through ensure_fixtures. Without this, a
        class that builds its fixtures directly (test_client's per-ttl interfaces)
        inherits whatever the site's Guardrails rows happen to say — on a freshly
        migrated site that is trap=Block everywhere, and every litellm-level mock
        without a nonce dies with "trap: nonce missing".

        Also forces Crema Settings' disabled check off (the real site may have it on),
        so no test accidentally sees a kill-switch CremaConfigError."""
        super().setUpClass()
        frappe.set_user("Administrator")
        _ensure_guardrails()
        settings = frappe.get_single("Crema Settings")
        if settings.get("disabled"):
            _MODIFIED.append(("Crema Settings", settings.name, {"disabled": 1}))
            settings.disabled = 0
            settings.save(ignore_permissions=True)
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — fixture must outlive this transaction

    @classmethod
    def ensure_fixtures(
        cls,
        *interface_names: str,
        users: tuple[str, ...] = (),
        guardrails: dict[str, str] | None = None,
        **interface_kw,
    ) -> None:
        """The setUpClass body most classes in the suite share: the isolation user, any
        extra users, the test provider, each named interface configured the same way,
        then the Crema Guardrails baseline (scan on, everything else off) overridden by
        `guardrails` — e.g. guardrails={"scan": "Off"}. A class that needs
        per-interface settings (test_client's three cache_ttls) or an extra fixture
        document calls the helpers directly instead — forcing those through here would
        be the abstraction this exists to avoid.
        """
        frappe.set_user("Administrator")
        _ensure_user(TEST_ISOLATION_USER)
        for user in users:
            _ensure_user(user)
        _ensure_provider()
        for name in interface_names:
            _ensure_interface(name, **interface_kw)
        _ensure_guardrails(**(guardrails or {}))
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — fixture must outlive this transaction

    def setUp(self) -> None:
        super().setUp()
        frappe.db.savepoint(self._SAVEPOINT)
        # Every class in the suite opened its own setUp with this; none sets a different
        # user, so it belongs here rather than in twenty copies.
        frappe.set_user("Administrator")

    def tearDown(self) -> None:
        frappe.db.rollback(save_point=self._SAVEPOINT)
        # A test that saved Crema Settings mid-test (e.g. via _ensure_interface) left
        # frappe's own document cache (get_cached_doc) holding the now-rolled-back
        # state — a DB rollback doesn't touch redis. Left stale, the next test's first
        # client._resolve() reads a config that no longer exists in the DB. Same story
        # for the Crema Guardrails Single a _set_guardrail call saved.
        frappe.clear_document_cache("Crema Settings")
        frappe.clear_document_cache("Crema Guardrails")
        cache.clear_interfaces()
        super().tearDown()

    @classmethod
    def tearDownClass(cls) -> None:
        cleanup_fixtures()
        super().tearDownClass()


def _ensure_user(email: str, roles: list[str] | None = None) -> str:
    if frappe.db.exists("User", email):
        doc = frappe.get_doc("User", email)
        existing_roles = {r.role for r in doc.get("roles")}
        new_roles = [r for r in (roles or []) if r not in existing_roles]
    else:
        doc = frappe.new_doc("User")
        doc.email = email
        doc.first_name = email.split("@")[0]
        doc.send_welcome_email = 0
        doc.enabled = 1
        doc.insert(ignore_permissions=True)
        _CREATED.append(("User", doc.name))
        new_roles = list(roles or [])
    for role in new_roles:
        doc.add_roles(role)
    if new_roles:
        _ROLES_GRANTED.append((doc.name, new_roles))
    return doc.name


def _ensure_provider() -> str:
    """The row shape lives in crema.testing.seed_provider now — this only adds this
    suite's own fixture-tracking on top, and never touches Crema Settings' defaults
    itself (set_defaults=False): several classes in this suite test the "not
    configured" state via _clear_defaults(), which a seeded default would fight."""
    if frappe.db.exists("Crema Provider", TEST_PROVIDER):
        return TEST_PROVIDER
    testing.seed_provider(TEST_PROVIDER, set_defaults=False)
    _CREATED.append(("Crema Provider", TEST_PROVIDER))
    return TEST_PROVIDER


_ASSIGNMENT_FIXTURE_FIELDS = (
    "provider",
    "model",
    "isolation_user",
    "system_prompt",
    "cache_ttl",
    "monthly_budget_usd",
)


def _ensure_interface(
    name: str,
    *,
    cache_ttl: int = 0,
    isolation_user: str = TEST_ISOLATION_USER,
    monthly_budget_usd: float = 0,
    model: str = "test-model",
) -> str:
    """Configure the Crema Model Assignment row for a PREDEFINED interface name on
    the single, global Crema Settings doc.

    Crema Settings is a Single: every interfaces.PREDEFINED name already has a row
    after install/migrate (CremaSettings.validate reconciles the child table to
    exactly that set), so — unlike the old per-document Crema Interface fixture —
    this always updates an existing row, never inserts a new one. Snapshots the
    row's prior field values into _MODIFIED so cleanup_fixtures() can restore them
    once the calling test class is done, exactly like a modified Crema Interface
    document used to be restored.

    The security posture is not per-interface anymore — see _set_guardrail /
    _ensure_guardrails for the Crema Guardrails side of a fixture.
    """
    if name not in interfaces.PREDEFINED:
        raise ValueError(f"'{name}' is not a predefined interface name")

    settings = frappe.get_single("Crema Settings")
    row = next(r for r in settings.assignments if r.interface == name)
    _MODIFIED.append(
        ("Crema Model Assignment", row.name, {f: row.get(f) for f in _ASSIGNMENT_FIXTURE_FIELDS})
    )

    row.provider = TEST_PROVIDER
    row.model = model
    row.isolation_user = isolation_user
    row.system_prompt = f"test system prompt for {name}"
    row.cache_ttl = cache_ttl
    row.monthly_budget_usd = monthly_budget_usd
    settings.save(ignore_permissions=True)
    return name


_GUARDRAIL_FIXTURE_FIELDS = ("action", "interfaces")

# The suite's baseline posture: the scan on everywhere (matching the old
# enable_prompt_scan=1 fixture default), everything else off — a test that wants the
# trap or masking switches exactly that one row on via _set_guardrail.
_GUARDRAIL_BASELINE = {"scan": "Block", "pi": "Off", "phi": "Off", "trap": "Off"}


def _ensure_guardrails(**actions: str) -> None:
    """Put the Crema Guardrails Single into the suite's baseline posture (see
    _GUARDRAIL_BASELINE), overridden per key by `actions` — e.g.
    _ensure_guardrails(trap="Block"). Snapshots every row it changes into _MODIFIED so
    cleanup_fixtures() restores the site's real posture afterwards."""
    doc = frappe.get_single("Crema Guardrails")
    desired = {**_GUARDRAIL_BASELINE, **actions}
    for row in doc.guardrails:
        if row.guardrail not in desired:
            continue
        _MODIFIED.append(("Crema Guardrail", row.name, {f: row.get(f) for f in _GUARDRAIL_FIXTURE_FIELDS}))
        row.action = desired[row.guardrail]
        row.interfaces = ""
    doc.save(ignore_permissions=True)


def _set_guardrail(key: str, action: str, *, interfaces_filter: str = "") -> None:
    """Set one Crema Guardrail row's action (and optionally its use-case filter),
    tracked for restore like _ensure_interface's row edits. Call after
    _ensure_guardrails() when a class needs a non-baseline row."""
    doc = frappe.get_single("Crema Guardrails")
    row = next(r for r in doc.guardrails if r.guardrail == key)
    _MODIFIED.append(("Crema Guardrail", row.name, {f: row.get(f) for f in _GUARDRAIL_FIXTURE_FIELDS}))
    row.action = action
    row.interfaces = interfaces_filter
    doc.save(ignore_permissions=True)


def _clear_defaults() -> None:
    """Blank Crema Settings' Default Provider/Model/Isolation User.

    A resolution test that expects a blank row to fall back (interfaces.FALLBACKS) or
    fail (CremaConfigError) is testing "not configured" — but the real site this suite
    runs against (see CLAUDE.md: `bench --site fcr.local run-tests`) may already have
    these set, and so may an earlier test in the same run. Call this from `setUp`, not
    `setUpClass`: CremaFixtureTestCase's per-test savepoint (or, for a plain
    IntegrationTestCase, its single class-level rollback) then undoes it same as any
    other test mutation.
    """
    settings = frappe.get_single("Crema Settings")
    settings.default_provider = ""
    settings.default_model = ""
    settings.default_isolation_user = ""
    settings.default_monthly_budget_usd = 0
    settings.default_monthly_budget_usd_per_user = 0
    settings.save(ignore_permissions=True)


def _assignment_row_name(interface: str) -> str:
    """The Crema Model Assignment child row name for `interface` — for tests that
    need to bypass Document validation via a raw frappe.db.set_value, the way the old
    orphan-provider test bypassed Crema Interface's own Link validation."""
    return frappe.db.get_value(
        "Crema Model Assignment", {"parent": "Crema Settings", "interface": interface}, "name"
    )


# ---------------------------------------------------------------------------
# document fixtures — tiny PDFs/images generated in-process, never read from disk
# ---------------------------------------------------------------------------


def _text_pdf_bytes(text: str = "Hello world. " * 20) -> bytes:
    """A PDF whose text layer is `text`. The default is well over _TEXT_PDF_MIN_CHARS,
    so prep_parts() treats it as a text PDF rather than a scanned one; a caller passing
    its own text has to keep it that long too."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _png_bytes(size: tuple[int, int] = (64, 64)) -> bytes:
    img = PILImage.new("RGB", size, color="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _scanned_pdf_bytes() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(50, 50, 200, 200))  # a shape, no text -> "scanned"
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _drop_advanced_ocr() -> None:
    """Also used as the "make sure advanced_ocr is NOT configured" setUp fixture, so it
    must clear the resolved-config cache unconditionally: client._resolve_one caches to
    redis with no TTL (client.py:60), and a per-test DB savepoint rollback undoes a
    previous test's _ensure_interface("advanced_ocr") row change but not that cache
    entry — leaving a stale "advanced_ocr is configured" cache hit for this test to
    trip over."""
    cache.clear_interfaces()
