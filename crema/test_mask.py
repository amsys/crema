"""Unit tests for crema.mask — EXPERIMENTAL reversible pseudonymisation (crema/mask.py's
module docstring names four guarantees; this file is organised around them). Pure
functions, no database needed — mirrors test_security.py's style.
"""

from __future__ import annotations

import random

from crema import mask
from crema.terms import TermGroup
from frappe.tests import UnitTestCase

# ---------------------------------------------------------------------------
# guarantee 1 — exact: restore(hide(text, v), v) == text, byte for byte.
# ---------------------------------------------------------------------------

CORPUS = [
    ("empty string", ""),
    ("whitespace only", "   \n\t  "),
    ("plain prose, nothing sensitive", "The invoice is due next quarter."),
    ("one bare email", "bob@example.com"),
    ("emoji and RTL marks", "hi \U0001f600 \u200e\u200f bye"),
    ("CJK text", "\u8acb\u78ba\u8a8d\u60a8\u7684\u8a02\u55ae\u7de8\u865f"),
    ("text containing a quote and a backslash", 'He said "call me" — path is C:\\temp\\x'),
    ("a JSON blob as plain text", '{"name": "Alice Wonderland", "email": "alice@x.com"}'),
    ("entirely one email", "alice@x.com"),
    ("mixed sensitive types", "Contact Alice Wonderland, alice@x.com, +230 5251 4412."),
    ("large document", ("Invoice for Jean-Claude Ramgoolam. " * 500) + "Email jc@acme.mu."),
]


class UnitTestCremaMaskRoundTrip(UnitTestCase):
    """Guarantee 1: hide() then restore() reproduces the input exactly, for every kind
    of input this table can think of, including inputs with nothing to mask at all."""

    def test_restore_of_hide_returns_input_unchanged(self):
        for label, text in CORPUS:
            with self.subTest(label=label):
                vault = mask.Vault()
                self.assertEqual(mask.restore(mask.hide(text, vault), vault), text)

    def test_randomised_corpus_round_trips_without_loss(self):
        # Seeded, so a failure reproduces exactly — stdlib random, no hypothesis dep.
        fragments = [
            "Jean-Claude Ramgoolam",
            "Mr Ramgoolam",
            "alice@example.com",
            "Bob+tag@example.com",
            "+230 5251 4412",
            "MU17BOMM0101101030300200000MUR",
            "4111 1111 1111 1111",
            "192.168.1.1",
            "plain prose about an invoice",
            "the quarterly report is due",
            "[[NAME_1]]",  # a literal, already-token-shaped fragment
            '"quoted" text',
        ]
        rng = random.Random(0)
        for i in range(200):
            text = " ".join(rng.choice(fragments) for _ in range(rng.randint(1, 8)))
            with self.subTest(i=i, text=text):
                vault = mask.Vault()
                self.assertEqual(mask.restore(mask.hide(text, vault), vault), text)

    def test_repeated_value_reuses_a_single_token(self):
        vault = mask.Vault()
        hidden = mask.hide("Email bob@x.com twice: bob@x.com again.", vault)
        self.assertEqual(hidden.count("[[EMAIL_1]]"), 2)
        self.assertEqual(len(vault.by_token), 1)

    def test_hide_does_not_mutate_the_input_messages(self):
        vault = mask.Vault()
        messages = [{"role": "user", "content": "Contact Alice Wonderland."}]
        mask.hide_messages(messages, vault)
        self.assertEqual(messages[0]["content"], "Contact Alice Wonderland.")


# ---------------------------------------------------------------------------
# guarantee 2 — never wrong: two different originals can never share a token.
# ---------------------------------------------------------------------------


class UnitTestCremaMaskCollisions(UnitTestCase):
    """Guarantee 2: a token always maps back to exactly the value that produced it."""

    def test_two_distinct_values_never_share_a_token(self):
        vault = mask.Vault()
        mask.hide("alice@x.com and bob@x.com", vault)
        self.assertNotEqual(vault.by_value["alice@x.com"], vault.by_value["bob@x.com"])

    def test_literal_token_in_input_cannot_hijack_restore(self):
        vault = mask.Vault()
        text = "The template shows [[NAME_1]] as an example, contact Alice Wonderland."
        hidden = mask.hide(text, vault)
        self.assertEqual(mask.restore(hidden, vault), text)

    def test_value_containing_a_quote_is_left_unmasked(self):
        vault = mask.Vault()
        text = 'Say "hi" to bob@x.com.'
        hidden = mask.hide(text, vault)
        self.assertIn('"hi"', hidden)  # untouched: masking it risks corrupting JSON mode
        self.assertNotIn("bob@x.com", hidden)  # the email on the same line still masks
        self.assertEqual(mask.restore(hidden, vault), text)

    def test_restore_leaves_an_unknown_token_untouched(self):
        vault = mask.Vault()
        mask.hide("Contact Alice Wonderland.", vault)
        restored = mask.restore("also see [[NAME_99]]", vault)
        self.assertIn("[[NAME_99]]", restored)

    def test_allowed_names_are_never_masked(self):
        vault = mask.Vault(allowed=["Acme Corp"])
        hidden = mask.hide("Acme Corp emailed jane@acme.mu.", vault)
        self.assertIn("Acme Corp", hidden)
        self.assertNotIn("jane@acme.mu", hidden)


# ---------------------------------------------------------------------------
# guarantee 3 — tolerant of transformation: restore recognises a spelling the model
# plausibly rewrote a token into.
# ---------------------------------------------------------------------------

RESTORE_HITS = [
    "[[NAME_1.2]]",
    "[[name_1.2]]",
    "[[NAME-1-2]]",
    "[[ NAME_1.2 ]]",
    "NAME_1.2",
    "NAME-1-2",
    "**NAME_1.2**",
    "NAME_1.2.",  # a real sentence-ending period, not part of the token
]
RESTORE_MISSES = [
    "NAME_12",  # a different entity's id, not this one's variant
    "EMAIL_1.2",  # right shape, label this vault never issued
    "NAME_1.2X",  # trailing garbage glued onto the id
]


class UnitTestCremaMaskRestoreTolerance(UnitTestCase):
    """Guarantee 3: restore() recognises a token through case, bracket and separator
    drift, but only for a label/entity/variant this vault actually issued."""

    def _vault_with_one_variant_token(self) -> mask.Vault:
        vault = mask.Vault()
        mask.hide("Mr Ramgoolam met Jean-Claude Ramgoolam.", vault)
        return vault

    def test_documented_restore_spellings_all_recover(self):
        for spelling in RESTORE_HITS:
            with self.subTest(spelling=spelling):
                vault = self._vault_with_one_variant_token()
                restored = mask.restore(spelling, vault)
                self.assertNotIn("NAME_1.2", restored.upper())
                self.assertFalse(vault.unresolved)

    def test_near_miss_spellings_do_not_recover(self):
        for spelling in RESTORE_MISSES:
            with self.subTest(spelling=spelling):
                vault = self._vault_with_one_variant_token()
                restored = mask.restore(spelling, vault)
                self.assertEqual(restored, spelling)

    def test_bare_token_restore_preserves_surrounding_whitespace(self):
        # Regression: an earlier version's optional-bracket regex ate the space after
        # a bare token even with no bracket present ('NAME_1 and' -> 'valueandrest').
        vault = mask.Vault()
        hidden = mask.hide("Mr Ramgoolam and Jean-Claude Ramgoolam.", vault)
        bare = hidden.replace("[[", "").replace("]]", "")
        restored = mask.restore(bare, vault)
        self.assertEqual(restored, "Mr Ramgoolam and Jean-Claude Ramgoolam.")

    def test_token_never_mentioned_again_is_not_flagged_unresolved(self):
        # A model that summarises away an entity entirely is normal, not a fault (see
        # mask.py's module docstring) — only a token-SHAPED fragment that fails to
        # resolve counts as guarantee 4's "loud failure".
        vault = mask.Vault()
        mask.hide("Alice Wonderland called about her invoice.", vault)
        restored = mask.restore("A customer called about an invoice.", vault)
        self.assertEqual(restored, "A customer called about an invoice.")
        self.assertFalse(vault.unresolved)


# ---------------------------------------------------------------------------
# detection — three sources, longest-first.
# ---------------------------------------------------------------------------

PATTERN_CASES = [
    ("email", "reach me at bob@example.com please", "EMAIL"),
    ("iban", "pay to MU17BOMM0101101030300200000MUR now", "IBAN"),
    ("card", "card 4111 1111 1111 1111 charged", "CARD"),
    ("phone", "call +230 5251 4412 today", "PHONE"),
    ("ip", "connect to 192.168.1.10 directly", "IP"),
]


class UnitTestCremaMaskDetection(UnitTestCase):
    """The pattern table and the capitalised-run sweep, as their own layer — see
    crema/mask.py's module docstring for why over-masking here is accepted."""

    def test_every_documented_pattern_case_matches(self):
        for label, text, expected in PATTERN_CASES:
            with self.subTest(label=label):
                vault = mask.Vault()
                hidden = mask.hide(text, vault)
                self.assertIn(f"[[{expected}_", hidden)

    def test_specific_pattern_wins_over_generic_phone(self):
        vault = mask.Vault()
        hidden = mask.hide("IBAN MU17BOMM0101101030300200000MUR", vault)
        self.assertIn("[[IBAN_", hidden)
        self.assertNotIn("[[PHONE_", hidden)

    def test_sweep_masks_a_capitalised_word_pair(self):
        vault = mask.Vault()
        hidden = mask.hide("Please contact Jean-Claude Ramgoolam soon.", vault)
        self.assertIn("[[NAME_", hidden)
        self.assertNotIn("Ramgoolam", hidden)

    def test_sweep_masks_single_name_after_a_title(self):
        vault = mask.Vault()
        hidden = mask.hide("Please see Dr Naidoo about this.", vault)
        self.assertIn("[[NAME_", hidden)
        self.assertNotIn("Naidoo", hidden)

    def test_sweep_skips_sentence_initial_single_word(self):
        vault = mask.Vault()
        hidden = mask.hide("Thursday is fine for the call.", vault)
        self.assertEqual(hidden, "Thursday is fine for the call.")

    def test_sweep_skips_a_stopword_only_run(self):
        vault = mask.Vault()
        hidden = mask.hide("Dear Sir, thank you for your order.", vault)
        self.assertEqual(hidden, "Dear Sir, thank you for your order.")

    def test_title_abbreviation_does_not_fuse_with_a_preceding_word(self):
        # Regression: an earlier version's word pattern let 'Mr'/'Ms'/'Dr' double as
        # an ordinary capitalised word, so 'Ask Mr Ramgoolam' swallowed 'Ask' into the
        # name token instead of leaving it as plain prose.
        vault = mask.Vault()
        hidden = mask.hide("Ask Mr Ramgoolam for the file.", vault)
        self.assertTrue(hidden.startswith("Ask "))

    def test_known_false_positive_is_pinned_as_current_behaviour(self):
        # "Royal Road" masks like any other two-capitalised-word run — the accepted
        # trade named in the plan: reversible, so wrong-but-safe, never fixed here.
        vault = mask.Vault()
        hidden = mask.hide("The office is on Royal Road.", vault)
        self.assertIn("[[NAME_", hidden)
        text = "The office is on Royal Road."
        self.assertEqual(mask.restore(hidden, vault), text)


# ---------------------------------------------------------------------------
# the PHI profile — hide()'s patterns/sweep parameters, as the "phi" guardrail in
# crema/guardrails.py passes them (_PHI_SENSITIVE, sweep=False).
# ---------------------------------------------------------------------------


class UnitTestCremaMaskPhiProfile(UnitTestCase):
    """hide()'s detection-profile parameters: mask._PHI_SENSITIVE with sweep=False is
    the PHI profile the "phi" guardrail uses; no arguments is the PI profile, which
    must stay byte-identical to the behaviour before the parameters existed."""

    def _phi_hide(self, text: str, vault: mask.Vault) -> str:
        return mask.hide(text, vault, patterns=mask._PHI_SENSITIVE, sweep=False)

    def test_health_keyword_masks_case_insensitively_and_round_trips(self):
        vault = mask.Vault()
        text = "Patient has DIABETES and needs insulin."
        hidden = self._phi_hide(text, vault)
        self.assertNotIn("DIABETES", hidden)
        self.assertNotIn("insulin", hidden)
        self.assertIn("[[PHI_", hidden)
        self.assertEqual(mask.restore(hidden, vault), text)

    def test_plural_form_of_a_term_masks(self):
        vault = mask.Vault()
        text = "She reported seizures last week."
        hidden = self._phi_hide(text, vault)
        self.assertNotIn("seizures", hidden)
        self.assertIn("[[PHI_", hidden)
        self.assertEqual(mask.restore(hidden, vault), text)

    def test_multi_word_term_masks_as_one_token(self):
        vault = mask.Vault()
        text = "His blood pressure is elevated."
        hidden = self._phi_hide(text, vault)
        self.assertNotIn("blood pressure", hidden)
        self.assertEqual(len(vault.by_token), 1)
        self.assertEqual(mask.restore(hidden, vault), text)

    def test_case_variants_get_distinct_tokens_in_one_entity(self):
        # "Diabetes" and "diabetes" must never share a token (guarantee 2), but
        # _pattern_entity_key groups them under one entity so the legend links them.
        vault = mask.Vault()
        text = "Diabetes runs in the family; diabetes management matters."
        hidden = self._phi_hide(text, vault)
        self.assertNotEqual(vault.by_value["Diabetes"], vault.by_value["diabetes"])
        self.assertEqual(len(vault.by_entity), 1)
        self.assertIn("same underlying value", mask.legend(vault))
        self.assertEqual(mask.restore(hidden, vault), text)

    def test_phi_profile_leaves_capitalised_names_unmasked(self):
        # sweep=False: health keywords name no people, so the capitalised-run sweep
        # is the PI profile's job, not this one's.
        vault = mask.Vault()
        hidden = self._phi_hide("Jean-Claude Ramgoolam has asthma.", vault)
        self.assertIn("Jean-Claude Ramgoolam", hidden)
        self.assertNotIn("asthma", hidden)


# ---------------------------------------------------------------------------
# phi_patterns/clean_terms — a site's own words (Crema Health Word) extending the
# built-in list, and the sanitiser that stands between a model's proposal and either.
# ---------------------------------------------------------------------------


class UnitTestCremaMaskPhiPatterns(UnitTestCase):
    """mask.phi_patterns: the built-in list plus a site's extra words, and the
    word-boundary split between space-separated and packed scripts."""

    def test_extra_word_joins_the_built_in_alternation(self):
        vault = mask.Vault()
        hidden = mask.hide(
            "Elle suit un traitement pour le diabète.",
            vault,
            patterns=mask.phi_patterns(("diabète",)),
            sweep=False,
        )
        self.assertNotIn("diabète", hidden)
        self.assertIn("[[PHI_", hidden)

    def test_extra_word_absent_by_default(self):
        vault = mask.Vault()
        hidden = mask.hide(
            "Elle suit un traitement pour le diabète.", vault, patterns=mask.phi_patterns(()), sweep=False
        )
        self.assertIn("diabète", hidden)

    def test_packed_script_term_matches_inside_running_text_without_a_boundary(self):
        # Chinese has no spaces between words, so \b never fires between two Hanzi —
        # the packed branch matches the substring directly instead of missing it.
        vault = mask.Vault()
        patterns = mask.phi_patterns(("糖尿病",))
        hidden = mask.hide("他患有糖尿病和高血压。", vault, patterns=patterns, sweep=False)
        self.assertNotIn("糖尿病", hidden)

    def test_spaced_term_still_requires_a_word_boundary(self):
        # "cancer" must not fire inside "cancerous" — the spaced branch keeps \b.
        vault = mask.Vault()
        hidden = mask.hide("a cancerous growth", vault, patterns=mask.phi_patterns(()), sweep=False)
        self.assertNotIn("[[PHI_", hidden)

    def test_result_is_cached_for_the_same_extra_tuple(self):
        self.assertIs(mask.phi_patterns(("foo",)), mask.phi_patterns(("foo",)))


class UnitTestCremaMaskCleanTerms(UnitTestCase):
    """mask.clean_terms: the sanitiser between a model's proposed word list
    (crema.crema.doctype.crema_health_word.translate_words) and anything that could
    reach a regex or the database."""

    def test_non_list_input_yields_no_words(self):
        self.assertEqual(mask.clean_terms(None), [])
        self.assertEqual(mask.clean_terms("diabetes"), [])
        self.assertEqual(mask.clean_terms({"words": ["diabetes"]}), [])

    def test_non_string_entries_are_dropped(self):
        self.assertEqual(mask.clean_terms(["diabetes", 42, None, ["nested"]]), ["diabetes"])

    def test_too_short_and_too_long_entries_are_dropped(self):
        self.assertEqual(mask.clean_terms(["ab", "cancer", "x" * 41]), ["cancer"])

    def test_entries_are_stripped_and_dedupe_case_insensitively(self):
        self.assertEqual(mask.clean_terms([" Diabetes ", "diabetes", "DIABETES "]), ["Diabetes"])

    def test_result_is_capped_at_the_limit(self):
        words = [f"word{i}" for i in range(10)]
        self.assertEqual(len(mask.clean_terms(words, limit=3)), 3)


# ---------------------------------------------------------------------------
# grouping — same entity, different spellings; never changes what restore returns.
# ---------------------------------------------------------------------------


class UnitTestCremaMaskGrouping(UnitTestCase):
    """Entity grouping across a value's surface forms — the mechanism that makes a
    coreference legend possible without ever merging two tokens (guarantee 2)."""

    def test_record_sourced_variants_share_one_entity(self):
        vault = mask.Vault(terms=[TermGroup(["Jean-Claude Ramgoolam", "J. Ramgoolam"], "NAME")])
        self.assertEqual(len(vault.by_entity), 1)

    def test_phone_variants_group_on_last_eight_digits(self):
        vault = mask.Vault()
        mask.hide("Call +230 5251 4412 or 5251-4412 again.", vault)
        self.assertEqual(len(vault.by_entity), 1)

    def test_email_case_and_plus_tag_group_together(self):
        vault = mask.Vault()
        mask.hide("bob@Example.com and Bob+newsletter@example.com", vault)
        self.assertEqual(len(vault.by_entity), 1)

    def test_two_people_sharing_a_surname_stay_separate(self):
        vault = mask.Vault()
        mask.hide("Marie Ramgoolam called about Jean Ramgoolam.", vault)
        self.assertEqual(len(vault.by_entity), 2)

    def test_ocr_drift_groups_with_its_clean_spelling(self):
        vault = mask.Vault()
        mask.hide("Jean-Claude Ramgoolam then Jean-Claude Ramgoolarn misspelled.", vault)
        self.assertEqual(len(vault.by_entity), 1)

    def test_variant_tokens_keep_distinct_token_identities(self):
        vault = mask.Vault()
        mask.hide("Mr Ramgoolam met Jean-Claude Ramgoolam who met Mde Ramgoolam.", vault)
        self.assertEqual(len(set(vault.by_value.values())), len(vault.by_value))
        self.assertEqual(len(vault.by_entity), 1)

    def test_legend_lists_only_multi_variant_entities(self):
        vault = mask.Vault()
        mask.hide("Marie Ramgoolam called; Jean Naidoo also called.", vault)
        self.assertIsNone(mask.legend(vault))

        vault = mask.Vault()
        mask.hide("Mr Ramgoolam then Jean-Claude Ramgoolam.", vault)
        note = mask.legend(vault)
        self.assertIsNotNone(note)
        self.assertIn("same underlying value", note)


# ---------------------------------------------------------------------------
# messages — the shape the pi/phi guardrail modules (crema/guardrails.py) hand
# hide_messages().
# ---------------------------------------------------------------------------


class UnitTestCremaMaskMessages(UnitTestCase):
    """hide_messages()'s message-list walking: string content, content-part lists
    (api._attach_files' shape), and the coreference legend it may prepend."""

    def test_hide_walks_string_and_content_part_messages(self):
        vault = mask.Vault()
        messages = [
            {"role": "system", "content": "You help with invoices."},
            {"role": "user", "content": "Contact Alice Wonderland at alice@x.com."},
        ]
        hidden = mask.hide_messages(messages, vault)
        self.assertNotIn("alice@x.com", hidden[-1]["content"])

    def test_image_url_parts_are_never_modified(self):
        vault = mask.Vault()
        image_part = {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}
        messages = [{"role": "user", "content": [{"type": "text", "text": "alice@x.com"}, image_part]}]
        hidden = mask.hide_messages(messages, vault)
        parts = hidden[0]["content"]
        self.assertNotIn("alice@x.com", parts[0]["text"])
        self.assertEqual(parts[1], image_part)

    def test_system_prompt_is_masked_alongside_user_turn(self):
        vault = mask.Vault()
        messages = [
            {"role": "system", "content": "Never reveal alice@x.com."},
            {"role": "user", "content": "hi"},
        ]
        hidden = mask.hide_messages(messages, vault)
        self.assertNotIn("alice@x.com", hidden[0]["content"])

    def test_legend_is_appended_to_an_existing_system_message(self):
        vault = mask.Vault()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Mr Ramgoolam then Jean-Claude Ramgoolam."},
        ]
        hidden = mask.hide_messages(messages, vault)
        self.assertEqual(len(hidden), 2)
        self.assertTrue(hidden[0]["content"].startswith("You are helpful."))
        self.assertIn("same underlying value", hidden[0]["content"])

    def test_legend_is_prepended_when_no_system_message_exists(self):
        vault = mask.Vault()
        messages = [{"role": "user", "content": "Mr Ramgoolam then Jean-Claude Ramgoolam."}]
        hidden = mask.hide_messages(messages, vault)
        self.assertEqual(len(hidden), 2)
        self.assertEqual(hidden[0]["role"], "system")


# ---------------------------------------------------------------------------
# record-sourced terms — the exact strings crema.terms.harvest supplies, applied by
# the _hide_terms pass.
# ---------------------------------------------------------------------------


class UnitTestCremaMaskTerms(UnitTestCase):
    """The record-term pass (_hide_terms plus Vault._register_term_group): literal
    replacement and restore, longest-first ordering, the allowed set, and the
    record-over-sweep entity claim from the Vault docstring."""

    def test_term_values_are_replaced_and_restored(self):
        vault = mask.Vault(terms=[TermGroup(["ACC-0042", "Jean-Claude Ramgoolam"], "NAME")])
        text = "Record ACC-0042 belongs to Jean-Claude Ramgoolam."
        hidden = mask.hide(text, vault)
        self.assertNotIn("ACC-0042", hidden)
        self.assertNotIn("Ramgoolam", hidden)
        self.assertEqual(mask.restore(hidden, vault), text)

    def test_longer_term_is_not_half_replaced_by_its_substring(self):
        # The docstring's own example: 'Acme Ltd' must mask as one value, not as
        # 'Acme''s token with a bare ' Ltd' left beside it.
        vault = mask.Vault(terms=[TermGroup(["Acme"], "NAME"), TermGroup(["Acme Ltd"], "NAME")])
        text = "Invoice from Acme Ltd arrived."
        hidden = mask.hide(text, vault)
        self.assertNotIn("Acme", hidden)
        self.assertNotIn("Ltd", hidden)
        self.assertEqual(mask.restore(hidden, vault), text)

    def test_allowed_value_is_never_registered_as_a_term(self):
        vault = mask.Vault(terms=[TermGroup(["Acme Ltd"], "NAME")], allowed=["Acme Ltd"])
        hidden = mask.hide("Acme Ltd sent the order.", vault)
        self.assertIn("Acme Ltd", hidden)

    def test_record_sourced_name_owns_the_entity_a_sweep_variant_joins(self):
        # "Mr Ramgoolam" is a sweep find, but the record term registered the surname
        # first — both spellings must land in the record's one entity, linked tokens.
        vault = mask.Vault(terms=[TermGroup(["Jean-Claude Ramgoolam"], "NAME")])
        text = "Jean-Claude Ramgoolam wrote; Mr Ramgoolam called again."
        hidden = mask.hide(text, vault)
        self.assertNotIn("Ramgoolam", hidden)
        self.assertEqual(len(vault.by_entity), 1)
        self.assertEqual(mask.restore(hidden, vault), text)
