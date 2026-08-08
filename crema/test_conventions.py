"""Meta tests: pin the test-suite conventions documented in CLAUDE.md (Conventions,
Tests section) so a future test can't drift back to a vague name or the
CremaFixtureTestCase footgun without the suite itself catching it.

Pure AST walk over crema/test_*.py — no database, no import of the modules under test.
"""

from __future__ import annotations

import ast
from pathlib import Path

from frappe.tests import UnitTestCase

_TEST_DIR = Path(__file__).parent

# Tokens that name nothing about the behavior under test — the mechanism ("table"-driven),
# a filler word, or a placeholder. Words that read as vague out of context but are actually
# this codebase's own vocabulary (an interface named "simple", a Crema "use case", a doctype
# "table" field, a dotted "path", `_parse_result`, "ok"/"work"/"valid" paired with a subject)
# are deliberately NOT here — banning them produced false positives against the real suite.
_CONTENTLESS_TOKENS = {
    "basic",
    "works",
    "stuff",
    "misc",
    "various",
    "foo",
    "bar",
    "baz",
    "todo",
    "tmp",
    "temp",
    "sanity",
    "smoke",
    "thing",
    "things",
    "stupid",
}
_CONTENTLESS_PHRASES = {"happy_path"}

_FIXTURE_HELPERS = {
    "_ensure_user",
    "_ensure_provider",
    "_ensure_interface",
    "_ensure_guardrails",
    "_set_guardrail",
}
_CLEANUP_BASE = "CremaFixtureTestCase"
_DB_BASES = {"IntegrationTestCase", _CLEANUP_BASE}
_UNIT_BASES = {"UnitTestCase"}


def _test_modules() -> list[tuple[Path, ast.Module]]:
    modules = []
    for path in sorted(_TEST_DIR.glob("test_*.py")):
        if path.name == "test_fixtures.py":
            continue  # holds no tests — see its own module docstring
        modules.append((path, ast.parse(path.read_text(), filename=str(path))))
    return modules


def _classes(tree: ast.Module) -> list[ast.ClassDef]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]


def _test_methods(cls: ast.ClassDef) -> list[ast.FunctionDef]:
    return [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]


def _base_names(cls: ast.ClassDef) -> set[str]:
    return {b.id for b in cls.bases if isinstance(b, ast.Name)}


def _references_fixture_helper(cls: ast.ClassDef) -> bool:
    return any(isinstance(n, ast.Name) and n.id in _FIXTURE_HELPERS for n in ast.walk(cls))


class UnitTestCremaTestConventions(UnitTestCase):
    """Pins the naming/structure rules in CLAUDE.md's Conventions section for
    crema/test_*.py itself — a test name or class shape that violates them fails here
    instead of surviving to code review."""

    def test_every_test_method_name_describes_a_behavior(self):
        violations = []
        for path, tree in _test_modules():
            for cls in _classes(tree):
                for method in _test_methods(cls):
                    words = method.name[len("test_") :].split("_")
                    reasons = []
                    if len(words) < 3:
                        reasons.append("fewer than 3 words after test_")
                    hit = [w for w in words if w in _CONTENTLESS_TOKENS]
                    if hit:
                        reasons.append(f"contentless word(s) {hit}")
                    if any(phrase in method.name for phrase in _CONTENTLESS_PHRASES):
                        reasons.append("contentless phrase")
                    if reasons:
                        violations.append(
                            f"{path.name}:{method.lineno} {cls.name}.{method.name} -- {'; '.join(reasons)}"
                        )
        self.assertEqual([], violations, "test name doesn't describe a behavior:\n" + "\n".join(violations))

    def test_no_two_tests_in_a_class_share_a_name(self):
        violations = []
        for path, tree in _test_modules():
            for cls in _classes(tree):
                seen = set()
                for method in _test_methods(cls):
                    if method.name in seen:
                        violations.append(f"{path.name}:{method.lineno} {cls.name}.{method.name} redefined")
                    seen.add(method.name)
        self.assertEqual(
            [], violations, "a redefined test silently shadows the earlier one:\n" + "\n".join(violations)
        )

    def test_every_test_class_is_named_for_its_tier(self):
        violations = []
        for path, tree in _test_modules():
            for cls in _classes(tree):
                bases = _base_names(cls)
                is_unit_name = cls.name.startswith("UnitTest")
                is_integration_name = cls.name.startswith("IntegrationTest")
                if not (is_unit_name or is_integration_name):
                    violations.append(
                        f"{path.name}:{cls.lineno} {cls.name} -- name doesn't start with "
                        "UnitTest or IntegrationTest"
                    )
                    continue
                if is_unit_name and bases & _DB_BASES:
                    violations.append(
                        f"{path.name}:{cls.lineno} {cls.name} -- named UnitTest* but "
                        f"subclasses a DB base ({bases & _DB_BASES})"
                    )
                if is_integration_name and bases & _UNIT_BASES:
                    violations.append(
                        f"{path.name}:{cls.lineno} {cls.name} -- named IntegrationTest* but "
                        f"subclasses UnitTestCase"
                    )
        self.assertEqual(
            [], violations, "test class name doesn't match its base class tier:\n" + "\n".join(violations)
        )

    def test_a_class_using_a_committing_fixture_helper_subclasses_the_cleanup_base(self):
        # _ensure_user/_ensure_provider/_ensure_interface commit outside the per-test
        # rollback (test_fixtures.py) -- only CremaFixtureTestCase undoes that in
        # tearDownClass. A plain IntegrationTestCase calling one of them leaks rows on
        # whatever site the suite runs against (CLAUDE.md's fcr.local footgun).
        violations = []
        for path, tree in _test_modules():
            for cls in _classes(tree):
                if _references_fixture_helper(cls) and _CLEANUP_BASE not in _base_names(cls):
                    violations.append(
                        f"{path.name}:{cls.lineno} {cls.name} -- calls a fixture helper "
                        f"but does not subclass {_CLEANUP_BASE}"
                    )
        self.assertEqual(
            [], violations, "fixture rows will leak past this class's tests:\n" + "\n".join(violations)
        )

    def test_every_test_class_carries_a_docstring(self):
        violations = []
        for path, tree in _test_modules():
            for cls in _classes(tree):
                if not ast.get_docstring(cls):
                    violations.append(f"{path.name}:{cls.lineno} {cls.name}")
        self.assertEqual(
            [], violations, "test class has no docstring naming what it covers:\n" + "\n".join(violations)
        )
