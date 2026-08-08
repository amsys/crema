"""Reversible pseudonymisation of sensitive values before they reach a provider —
EXPERIMENTAL (the "pi" and "phi" guardrails in crema/guardrails.py, default Off; see
docs/security.md). Pseudonymisation, not anonymisation: the value↔token map lives on
this side, so this is not a legal or compliance control (docs/security.md says so to
the user; keep it that way).

Pure: no frappe import, no I/O — like crema/security.py, unit-testable with
UnitTestCase, and safe to import from crema.terms (which is not pure).

Four guarantees this module exists to hold, in order of what breaks if it doesn't:

1. Exact — restore(hide(text, v), v) == text, byte for byte, for any input.
2. Never wrong — two different originals can never share a token, so a token can
   never restore to somebody else's data.
3. Tolerant of transformation — the model will summarise, translate and restructure
   around a token; restore() still finds it through case changes, dropped brackets
   and separator drift.
4. Loud when it fails — a token-shaped fragment that could not be resolved is
   reported (Vault.unresolved), never silently left in the returned text.

Guarantee 4 exists because a fifth one — that a model never paraphrases a token out
of existence entirely — is not achievable, and pretending otherwise would be the
actual fault. A token the model simply never mentions again is not a failure; there
is nothing token-shaped left to report. Only a fragment that IS still in the
response, but didn't resolve, counts. That is why restore() tracks misses itself,
during its one substitution pass, rather than re-scanning the final text afterwards:
a second independent scan cannot tell a genuinely unresolved token apart from a
caller's own literal "[[NAME_1]]"-looking text correctly round-tripped back into the
answer (see _hide_literals) — both look identical to a dumb regex once restored.

Detection is deliberately tuned to over-mask: because reversal is exact, a false
positive only costs the model some comprehension. A false negative ships a real name
to a third party, permanently. See the ceilings called out at each pass below.
"""

from __future__ import annotations

import difflib
import functools
import re
from collections.abc import Iterable

# A token this module emits and recognises as its own: [[LABEL_123]] or
# [[LABEL_123.4]]. Used to fence every hide pass off from tokens earlier passes (or an
# earlier hide_messages call sharing this vault) already emitted — see
# _map_outside_tokens. Deliberately strict (exactly two brackets) so it only ever
# matches crema's own output, never restore()'s looser recognition pattern below.
_TOKEN_RE = re.compile(r"\[\[[A-Z]+_\d+(?:\.\d+)?\]\]")


def _map_outside_tokens(text: str, fn) -> str:
    """Apply fn(chunk) -> chunk to every substring of `text` NOT already inside an
    emitted [[LABEL_n]] token, leaving existing tokens untouched.

    This is the structural guarantee that "no pass can corrupt an earlier one" isn't
    just a matter of pattern discipline: a token minted by an earlier pass (or an
    earlier field in the same hide_messages call) is masked out of every later pass by
    construction, the same way _canon in security.py is a pure function applied once.
    """
    out = []
    pos = 0
    for m in _TOKEN_RE.finditer(text):
        out.append(fn(text[pos : m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(fn(text[pos:]))
    return "".join(out)


# ---------------------------------------------------------------------------
# Vault — the in-memory index. One per client._complete call. Never logged, never
# persisted, never sent to a provider; it dies with the call that built it.
# ---------------------------------------------------------------------------


class Vault:
    """Bijective value<->token index, plus the entity/variant grouping that lets a
    coreference legend tell the model "these tokens are the same underlying value"
    without ever merging their tokens (see the module docstring's guarantee 2).

    `terms`: TermGroup-shaped objects (crema.terms.TermGroup — duck-typed here as
    "has .values: list[str] and .label: str" so this module stays frappe-free)
    pre-registered at construction, so a frappe-record-sourced name always wins entity
    assignment over a same-named match the capitalised sweep finds later.

    `allowed`: values never masked, however matched — e.g. the site's own company
    name, so it isn't masked out of every prompt (see client._allowed_names).
    """

    def __init__(self, terms: Iterable = (), allowed: Iterable[str] = ()):
        self.allowed = frozenset(v for v in allowed if v)
        self.by_token: dict[str, str] = {}
        self.by_value: dict[str, str] = {}
        self.by_entity: dict[int, list[str]] = {}
        self.unresolved: list[str] = []  # guarantee 4, filled by restore()

        self._entity_of: dict[tuple[str, str], int] = {}  # (label, key) -> entity id
        self._name_keys: set[str] = set()  # normalised NAME keys seen so far
        self._next_entity = 1

        for group in terms:
            self._register_term_group(group)

    # -- writing -------------------------------------------------------------------

    def _new_entity(self) -> int:
        eid = self._next_entity
        self._next_entity += 1
        return eid

    def _entity_id(self, label: str, entity_key: str | None) -> int:
        if entity_key is None:
            return self._new_entity()
        key = (label, entity_key)
        if key not in self._entity_of:
            self._entity_of[key] = self._new_entity()
        return self._entity_of[key]

    def _emit(self, value: str, label: str, eid: int) -> str:
        if value in self.by_value:
            return self.by_value[value]
        variants = self.by_entity.setdefault(eid, [])
        n = len(variants) + 1
        token = f"[[{label}_{eid}]]" if n == 1 else f"[[{label}_{eid}.{n}]]"
        self.by_token[token] = value
        self.by_value[value] = token
        variants.append(token)
        return token

    def _register_term_group(self, group) -> None:
        """One record's surface forms share one entity, by construction — the exact
        tier of grouping (see mask.py's module docstring / crema.terms)."""
        eid = self._new_entity()
        for value in group.values:
            if not value or value in self.allowed or '"' in value or "\\" in value:
                continue
            self._emit(value, group.label, eid)
            if group.label == "NAME":
                key = _name_key(value)
                self._entity_of[("NAME", key)] = eid
                self._name_keys.add(key)

    def token_for(self, value: str, label: str, entity_key: str | None = None) -> str | None:
        """A token for `value`, or None to leave it unmasked: allowlisted, or
        containing a `"`/`\\` that would corrupt a JSON-mode response on restore (see
        the module docstring's collision guards — no pattern below can itself match a
        quote, this only guards a future one that could)."""
        if value in self.allowed or '"' in value or "\\" in value:
            return None
        if value in self.by_value:
            return self.by_value[value]
        eid = self._entity_id(label, entity_key)
        return self._emit(value, label, eid)

    def name_entity_key(self, raw: str) -> str:
        """The entity key `raw` (a capitalised-sweep name candidate) should group
        under: an existing NAME entity's key if `raw` plausibly refers to the same
        person (word-set containment, or close OCR-drift spelling), else raw's own
        normalised key, opening a new entity.

        Best-effort by design (see mask.py's module docstring): over-merging here
        costs the model comprehension, never correctness — restore is still exact
        either way, because tokens (not values) are what identifies an entity.
        """
        norm = _name_key(raw)
        if norm in self._name_keys:
            return norm
        norm_tokens = set(norm.split())
        for key in self._name_keys:
            key_tokens = set(key.split())
            shared = norm_tokens & key_tokens
            long_shared = any(len(t) >= 3 for t in shared)
            contains = norm_tokens and key_tokens and (norm_tokens <= key_tokens or key_tokens <= norm_tokens)
            close = difflib.SequenceMatcher(None, norm, key).ratio() >= 0.9
            if (contains and long_shared) or len(shared) >= 2 or close:
                self._name_keys.add(norm)
                return key
        self._name_keys.add(norm)
        return norm


_TITLES = frozenset({"mr", "mrs", "ms", "mme", "dr", "m"})


def _name_key(raw: str) -> str:
    """Casefold + transliterate + drop titles/punctuation, so 'Mr Ramgoolam' and
    'RAMGOOLAM' normalise to the same word set. anyascii, not a bare .lower(): the
    sweep needs to group an accented or non-Latin spelling with its ASCII one, the
    same reasoning security._canon gives for layer 1."""
    from anyascii import anyascii

    words = re.findall(r"[a-z]+", anyascii(raw).casefold())
    return " ".join(w for w in words if w not in _TITLES)


# ---------------------------------------------------------------------------
# Detection — three sources, applied in this order: terms, then patterns, then the
# capitalised-run sweep. Each wrapped in _map_outside_tokens, so a token an earlier
# source already minted is inert to every source that runs after it.
# ---------------------------------------------------------------------------

# Specific before generic: PHONE's `\d[\d ()-]{7,}\d` would otherwise eat every
# IBAN/CARD/NID's digits. Same first-match-matters discipline as
# security._INJECTION_PATTERNS, applied per-pass rather than first-match-wins overall.
_SENSITIVE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "EMAIL"),
    (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"), "IBAN"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "CARD"),
    (re.compile(r"\b[A-Z]\d{6}[A-Z0-9]{6}\b"), "NID"),  # Mauritian NIC shape
    (re.compile(r"\+?\d[\d ()-]{7,}\d"), "PHONE"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "IP"),
]


def _pattern_entity_key(value: str, label: str) -> str | None:
    """Deterministic grouping for a structured identifier's own spelling variants —
    the second tier in mask.py's module docstring. IBAN/CARD/NID/IP get no
    normalisation here: same string -> same token already, via Vault.by_value, and a
    different string is a different identifier, not a variant of one."""
    if label == "PHONE":
        digits = re.sub(r"\D", "", value)
        return digits[-8:] if len(digits) >= 8 else digits or None
    if label == "EMAIL":
        local, _, domain = value.partition("@")
        return f"{local.split('+', 1)[0].casefold()}@{domain.casefold()}"
    if label == "PHI":
        # "Diabetes" and "diabetes" are one term, not two identifiers — group the
        # spelling variants so the legend links their tokens.
        return value.casefold()
    return None


# PHI — Personal Health Information, the second masking guardrail (crema.guardrails).
# A curated keyword/pattern list, NOT a health-information detector: it hides the common
# condition/medication/procedure words so a provider cannot associate a person with a
# health fact, and it will miss anything phrased outside this list. Plural "s" is
# accepted on every term; matching is case-insensitive, and _pattern_entity_key groups
# spelling variants of one term under one entity so the legend links them.
_PHI_TERMS = (
    "anemia",
    "antibiotic",
    "antidepressant",
    "arthritis",
    "asthma",
    "autism",
    "biopsy",
    "bipolar",
    "blood pressure",
    "blood test",
    "cancer",
    "cardiac",
    "chemotherapy",
    "cholesterol",
    "chronic pain",
    "cirrhosis",
    "concussion",
    "dementia",
    "depression",
    "dermatitis",
    "diabetes",
    "diagnosis",
    "dialysis",
    "disability",
    "eczema",
    "epilepsy",
    "fracture",
    "hepatitis",
    "hiv",
    "hypertension",
    "infection",
    "influenza",
    "insulin",
    "kidney disease",
    "leukemia",
    "medication",
    "mental health",
    "migraine",
    "miscarriage",
    "morphine",
    "obesity",
    "oncology",
    "overdose",
    "pacemaker",
    "palliative",
    "parkinson",
    "pneumonia",
    "pregnancy",
    "pregnant",
    "prescription",
    "prognosis",
    "psychiatric",
    "radiotherapy",
    "schizophrenia",
    "seizure",
    "sexually transmitted",
    "stroke",
    "surgery",
    "transplant",
    "tuberculosis",
    "tumor",
    "tumour",
    "vaccination",
    "vaccine",
    "wheelchair",
    "x-ray",
)

# \b marks a boundary only where a script separates its words with spaces. Chinese,
# Japanese and Thai text packs words together with no separators, so \b never fires
# between two of a script's own characters — a term in one of those scripts is matched
# without a boundary instead, which over-masks rather than silently matching nothing.
# That trade mirrors the module docstring's guarantee 4: over-masking costs the model
# some comprehension, under-masking ships a real health fact to a third party.
_NO_WORD_BREAKS = re.compile(r"[぀-ヿ㐀-鿿豈-﫿฀-๿]")


@functools.lru_cache(maxsize=8)
def phi_patterns(extra: tuple[str, ...] = ()) -> list[tuple[re.Pattern[str], str]]:
    """The PHI alternation: the built-in _PHI_TERMS plus a site's own words (Crema
    Health Word, read by crema.guardrails._phi_patterns), longest-first so a
    multi-word term wins over a shorter one it contains. Cached on `extra`, so a
    request that hasn't changed the word list pays a dict lookup, not a regex
    recompile — `_PHI_SENSITIVE` below is this function's built-in-only value.

    ponytail: still a keyword list, not a detector — a French plural
    ("diabétiques") or a Russian case still misses; a per-language stemmer is the
    upgrade, not a bigger word list."""
    terms = sorted({*_PHI_TERMS, *extra}, key=len, reverse=True)
    packed = [t for t in terms if _NO_WORD_BREAKS.search(t)]
    spaced = [t for t in terms if not _NO_WORD_BREAKS.search(t)]

    patterns: list[tuple[re.Pattern[str], str]] = []
    if spaced:
        body = "|".join(r"\s+".join(re.escape(word) for word in t.split()) for t in spaced)
        patterns.append((re.compile(rf"\b(?:{body})s?\b", re.IGNORECASE), "PHI"))
    if packed:
        patterns.append((re.compile("|".join(re.escape(t) for t in packed), re.IGNORECASE), "PHI"))
    return patterns


_PHI_SENSITIVE: list[tuple[re.Pattern[str], str]] = phi_patterns()


def clean_terms(raw: object, limit: int = 200) -> list[str]:
    """Sanitise a model's proposed word list before it can reach a regex: keep only
    strings, strip whitespace, drop anything under 3 or over 40 characters, dedupe
    case-insensitively, cap at `limit`. Never trusts a model's JSON shape or
    content — see crema.crema.doctype.crema_health_word.translate_words."""
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        word = item.strip()
        if not (3 <= len(word) <= 40):
            continue
        key = word.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(word)
        if len(cleaned) >= limit:
            break
    return cleaned


def _hide_patterns(text: str, vault: Vault, patterns: list[tuple[re.Pattern[str], str]]) -> str:
    for pattern, label in patterns:

        def repl(m: re.Match, label: str = label) -> str:
            value = m.group(0)
            token = vault.token_for(value, label, _pattern_entity_key(value, label))
            return token if token else value

        text = _map_outside_tokens(text, lambda chunk, p=pattern, r=repl: p.sub(r, chunk))
    return text


def _hide_terms(text: str, vault: Vault) -> str:
    """Every term Vault was constructed with is already tokenised — this pass just
    finds their literal spelling in `text`, longest first so 'Acme Ltd' isn't half-
    replaced by a shorter term that happens to be its substring ('Acme')."""
    terms = sorted((t for t in vault.by_value if t), key=len, reverse=True)
    if not terms:
        return text

    def repl(chunk: str) -> str:
        for term in terms:
            if term in chunk:
                chunk = chunk.replace(term, vault.by_value[term])
        return chunk

    return _map_outside_tokens(text, repl)


# Runs of 2+ capitalised words, or a title followed by 1+ capitalised words. A bare
# single capitalised word never matches — which is what keeps an ordinary
# sentence-initial word out, with no separate stopword check needed for that case.
# _CAP_WORD requires 2+ lowercase letters after the capital (3+ chars total),
# specifically so "Mr"/"Ms"/"Dr" can't double as an ordinary word in the no-title
# branch and get fused onto whatever precedes them ("Ask Mr Ramgoolam" swallowing
# "Ask") — those abbreviations are exactly what _TITLE_WORD exists to recognise on
# its own terms.
_TITLE_WORD = r"(?:Mr|Mrs|Ms|Mme|Dr|M)\.?"
# The character class below includes a real curly apostrophe, not a lookalike — a name
# like the curly-quote spelling of "O'Brien".
_CAP_WORD = r"[A-Z][a-z]{2,}(?:['’-][A-Z][a-z]+)*"  # noqa: RUF001
_SWEEP_RE = re.compile(
    rf"\b(?:{_TITLE_WORD}\s+{_CAP_WORD}(?:\s+{_CAP_WORD})*|{_CAP_WORD}(?:\s+{_CAP_WORD})+)\b"
)

# Weekdays, months and the handful of words that legitimately open a sentence in two
# capitalised words ("Dear Sir", "Thank You") — a run entirely made of these is
# skipped. Deliberately small: see mask.py's module docstring on why over-masking
# elsewhere in the sweep is accepted, not fixed.
_STOPWORDS = frozenset(
    """monday tuesday wednesday thursday friday saturday sunday
    january february march april may june july august september october november december
    dear hello hi thank thanks regards sincerely best please note warning important attention sir""".split()
)


def _hide_sweep(text: str, vault: Vault) -> str:
    def repl(chunk: str) -> str:
        def sub(m: re.Match) -> str:
            value = m.group(0)
            words = re.findall(r"[A-Za-z']+", value)
            if all(w.casefold().rstrip(".") in _STOPWORDS for w in words):
                return value
            token = vault.token_for(value, "NAME", vault.name_entity_key(value))
            return token if token else value

        return _SWEEP_RE.sub(sub, chunk)

    return _map_outside_tokens(text, repl)


def _hide_literals(text: str, vault: Vault) -> str:
    """Mask any [[LABEL_n]]-shaped text already present in the INPUT, before any real
    token is minted — the first collision guard from the module docstring. Without
    this, a document that literally contains '[[NAME_1]]' could later collide with (or
    be mistaken for) a token this module assigns for a real name."""

    def repl(m: re.Match) -> str:
        original = m.group(0)
        token = vault.token_for(original, "LITERAL", entity_key=original)
        return token if token else original

    return _TOKEN_RE.sub(repl, text)


def hide(
    text: str,
    vault: Vault,
    patterns: list[tuple[re.Pattern[str], str]] = _SENSITIVE,
    sweep: bool = True,
) -> str:
    """Mask `text` against `vault`, in place conceptually — `text` itself is
    never mutated (it's a str); returns the masked copy.

    `patterns`/`sweep` select the detection profile: the defaults are the PI profile
    (structured identifiers plus the capitalised-name sweep); the PHI guardrail passes
    _PHI_SENSITIVE with sweep=False (health keywords name no people)."""
    if not text:
        return text
    text = _hide_literals(text, vault)
    text = _hide_terms(text, vault)
    text = _hide_patterns(text, vault, patterns)
    if sweep:
        text = _hide_sweep(text, vault)
    return text


def hide_messages(
    messages: list[dict],
    vault: Vault,
    patterns: list[tuple[re.Pattern[str], str]] = _SENSITIVE,
    sweep: bool = True,
) -> list[dict]:
    """Anonymise every text-bearing part of `messages` against `vault`, returning a
    NEW list (mirrors the reply check's no-mutation contract) — a content-part list
    (see api._attach_files) has `type == "image_url"` parts left untouched, since
    masking is a text-only control (see crema.guardrails).

    `patterns`/`sweep` pass straight through to hide(). Appends a coreference legend
    as a new system turn (or onto the existing one) when `vault` holds any
    multi-variant entity — see legend().
    """
    out = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            out.append({**msg, "content": hide(content, vault, patterns, sweep)})
        elif isinstance(content, list):
            parts = [
                {**part, "text": hide(part.get("text", ""), vault, patterns, sweep)}
                if part.get("type") == "text"
                else part
                for part in content
            ]
            out.append({**msg, "content": parts})
        else:
            out.append(msg)

    note = legend(vault)
    if not note:
        return out
    if out and out[0].get("role") == "system":
        out[0] = {**out[0], "content": f"{out[0]['content']}\n\n{note}"}
        return out
    return [{"role": "system", "content": note}, *out]


def legend(vault: Vault) -> str | None:
    """One line naming every multi-variant entity as coreferent, or None when every
    entity in `vault` is a singleton — the common case, which costs no extra tokens."""
    groups = [tokens for tokens in vault.by_entity.values() if len(tokens) > 1]
    if not groups:
        return None
    lines = (f"{', '.join(tokens)} refer to the same underlying value." for tokens in groups)
    return "Coreference note: " + " ".join(lines)


# ---------------------------------------------------------------------------
# Restore — the loose, tolerant ladder (guarantee 3), scoped to only the labels this
# vault actually issued (guarantee 2's other half: a coincidental "CARD 5" in
# unrelated prose can't restore to anything, because no such token exists to find).
# ---------------------------------------------------------------------------


def _restore_pattern(labels: Iterable[str]) -> re.Pattern[str] | None:
    ordered = sorted(set(labels), key=len, reverse=True)
    if not ordered:
        return None
    alt = "|".join(re.escape(label) for label in ordered)
    # Two whole branches — bracketed and bare — rather than independently optional
    # bracket/whitespace tokens: an EARLIER version made the brackets optional but the
    # surrounding \s* unconditional, so a bare 'NAME_1 and NAME_2' lost the space
    # after NAME_1 (the closing \s*\]{0,2} ate it even with no bracket to justify
    # eating it), gluing the next word onto the restored value. Two clean branches
    # means whitespace is only ever consumed where a bracket is actually present.
    # The variant group is greedy, so '[[NAME_1.2]]' resolves as one match, never
    # split into a bare 'NAME_1' plus a dangling '.2' — the concern a strict longest-
    # token-first ordering would otherwise exist to solve.
    #
    # The digit/variant group is wrapped in an ATOMIC group (?>...), not left to plain
    # backtracking: without it, a bare 'NAME_1.2X' first tries the full '1.2' variant,
    # fails the trailing \b (a digit followed by a letter has no boundary), then
    # BACKTRACKS to the shorter 'NAME_1' — which does have a boundary before '.' — and
    # wrongly restores as if '.2X' were unrelated trailing text. Atomic makes that
    # shorter fallback unavailable: the whole span either matches cleanly (including a
    # real trailing '.' as ordinary punctuation, not part of the token) or not at all.
    #
    # Two capture groups per branch (label, entity, variant) rather than one shared
    # set: Python's `re` won't let the same group name/number carry different meaning
    # per alternation branch, so repl() below reads group(1..3) or group(4..6),
    # whichever branch actually matched.
    return re.compile(
        rf"\[\[\s*({alt})[_\-\s](?>(\d+)(?:[.\-](\d+))?)\s*\]\]"
        rf"|\b({alt})[_\-\s](?>(\d+)(?:[.\-](\d+))?)\b",
        re.IGNORECASE,
    )


def restore(text: str, vault: Vault) -> str:
    """Reverse hide()/hide_messages() against `vault`. Exact for anything the model
    echoed in a recognised spelling (guarantees 1 and 3); anything token-shaped that
    still doesn't resolve is left as-is in the returned text AND appended to
    `vault.unresolved` (guarantee 4) — see the module docstring for why that tracking
    happens here, during the one substitution pass, rather than as a second scan.
    """
    if not vault.by_token:
        return text

    labels = {token[2 : token.index("_", 2)] for token in vault.by_token}
    pattern = _restore_pattern(labels)
    if pattern is None:
        return text

    def repl(m: re.Match) -> str:
        label = (m.group(1) or m.group(4)).upper()
        entity = m.group(2) or m.group(5)
        variant = m.group(3) or m.group(6)
        canonical = f"[[{label}_{entity}.{variant}]]" if variant else f"[[{label}_{entity}]]"
        found = vault.by_token.get(canonical)
        if found is None:
            vault.unresolved.append(m.group(0))
            return m.group(0)
        return found

    return pattern.sub(repl, text)
