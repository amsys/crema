"""Security scanning — layer 1 (regex/unicode) and layer 2 (LLM guard) glue.

Layer 1 (`scan`) is pure: no frappe imports, no I/O, just a canonicalization pass and
compiled regexes over the prompt/context strings. Layer 2 (the LLM guard) is wired into
`crema.api.ask` via a recursive `ask("security", ...)` call — see the
`# phase 3: llm guard` seam there.
"""

from __future__ import annotations

import re
import unicodedata

from anyascii import anyascii
from ftfy import fix_text

# Format characters that stay legal: U+200E/U+200F (directional marks) and U+200C/U+200D
# (ZWNJ/ZWJ), legitimate in Arabic/Indic shaping and emoji ZWJ sequences. Written as
# escapes, never as literal invisible/bidi characters in this source file.
_ALLOWED_FORMAT = "\u200c\u200d\u200e\u200f"

# The deprecated tag block, spelled as a range rather than by category: 31 of its
# codepoints are unassigned (Cn) rather than Cf, so a category test alone misses them.
_TAG_BLOCK = range(0xE0000, 0xE0080)

# Control/format/private-use categories to strip before matching. Cc contains \t \n \r,
# and deleting those welds words together \u2014 "forget all\nprevious instructions" becomes
# "forget allprevious instructions", which defeats every pattern below with a literal \s+
# between two fixed words, so _canon keeps those three. (An attacker splitting a word with
# U+200C/U+200D gets it closed up there; every other Cf codepoint has already been blocked
# outright by _is_blocked_char.)
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Co"})


def _is_blocked_char(c: str) -> bool:
    """True for an invisible/control character no legitimate prompt needs.

    Categories come from `unicodedata`, i.e. the Unicode database CPython ships, rather
    than a hand-written table of escape ranges that would need regenerating by hand
    whenever Unicode assigns a new format character.
    """
    return (unicodedata.category(c) == "Cf" and c not in _ALLOWED_FORMAT) or ord(c) in _TAG_BLOCK


# Case-insensitive injection patterns. Module-level list — trivially extensible,
# add a (pattern, reason) tuple. First match wins; order doesn't matter for blocking.
#
# re.S (DOTALL) matters as much as re.I: every gap here is a bounded `.{0,N}?`, and
# without DOTALL a line break anywhere inside that gap defeats the pattern — so
# "ignore all\nprevious instructions" would sail through, whether the break came from
# the attacker's own text or from scan()'s context/prompt join.
_INJECTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"ignore\s+.{0,15}?(previous|above|prior|earlier)\s+instructions", re.I | re.S),
        "prompt injection: ignore-instructions",
    ),
    (
        re.compile(r"disregard\s+.{0,20}?(system\s+prompt|instructions)", re.I | re.S),
        "prompt injection: disregard-instructions",
    ),
    (
        re.compile(r"forget\s+(all\s+|any\s+)?(previous|prior|above)\s+instructions", re.I | re.S),
        "prompt injection: forget-instructions",
    ),
    # Requires a role-ish continuation (a/an/the/no longer/operating/acting) rather than
    # matching bare "you are now" — that bare form is a documented false positive on
    # ordinary sentences like "You are now able to submit the form." (see test_security.py).
    # "You are now a member of the team" is a known remaining false positive: narrowed,
    # not cured.
    (
        re.compile(r"\byou\s+are\s+now\s+(a|an|the|no\s+longer|operating|acting)\b", re.I | re.S),
        "prompt injection: role override",
    ),
    (
        re.compile(r"from\s+now\s+on\s*,?\s+you\s+(are|will|must|shall)\b", re.I | re.S),
        "prompt injection: role override",
    ),
    (re.compile(r"pretend\s+(you\s+are|to\s+be)\b", re.I | re.S), "prompt injection: pretend-to-be"),
    (re.compile(r"reveal\s+.{0,20}?system\s+prompt", re.I | re.S), "prompt injection: reveal system prompt"),
    (
        re.compile(r"(show|print|output|repeat|leak)\s+.{0,20}?system\s+prompt", re.I | re.S),
        "prompt injection: exfiltrate system prompt",
    ),
    (
        re.compile(r"what\s+(is|are)\s+your\s+(system\s+prompt|instructions)", re.I | re.S),
        "prompt injection: exfiltrate system prompt",
    ),
    (re.compile(r"\bdeveloper\s+mode\b", re.I | re.S), "prompt injection: developer mode"),
    (re.compile(r"\bjailbreak(ing|ed)?\b", re.I | re.S), "prompt injection: jailbreak"),
    (re.compile(r"\bdo\s+anything\s+now\b", re.I | re.S), "prompt injection: DAN"),
    (
        re.compile(r"act\s+as\s+.{0,20}?\b(dan|an?\s+unrestricted|jailbroken)\b", re.I | re.S),
        "prompt injection: act-as unrestricted persona",
    ),
    (re.compile(r"new\s+instructions?\s*:", re.I | re.S), "prompt injection: new-instructions marker"),
    (re.compile(r"###\s*(system|assistant)\b", re.I | re.S), "prompt injection: fake system marker"),
    (
        re.compile(
            r"<\|(im_start|im_end|start_header_id|end_header_id|eot_id|endoftext|system|user|assistant)\|>",
            re.I | re.S,
        ),
        "prompt injection: chat-template token",
    ),
    (re.compile(r"<<\s*sys\s*>>|\[/?inst\]", re.I | re.S), "prompt injection: chat-template token"),
    (re.compile(r"\[\s*system\s*\]", re.I | re.S), "prompt injection: fake system marker"),
    # Appended after every pre-existing pattern (rather than interleaved) so first-match-
    # wins precedence for every case already pinned in test_security.py's CASES table is
    # undisturbed — e.g. "act as DAN and ignore your guidelines" must still report
    # "act-as unrestricted persona", not this override-guidelines pattern below.
    (
        re.compile(
            r"your\s+(initial|original|hidden|full|complete)\s+(instructions?|prompt|system\s+message)",
            re.I | re.S,
        ),
        "prompt injection: exfiltrate system prompt",
    ),
    (
        re.compile(
            # One pattern wrapped across two lines, not two list elements.
            # nosemgrep: string-concat-in-list
            r"(repeat|print|output|show|display)\s+.{0,20}?\b(everything|all\s+(of\s+)?the\s+text)\s+"
            r"(above|before|preceding)\b",
            re.I | re.S,
        ),
        "prompt injection: exfiltrate prior context",
    ),
    (
        re.compile(
            # One pattern wrapped across two lines, not two list elements.
            # nosemgrep: string-concat-in-list
            r"(override|bypass|circumvent|disable|ignore)\s+(all\s+|any\s+)?your\s+.{0,20}?\b"
            r"(instructions?|restrictions?|guidelines?|rules?|filters?|safety|programming|training)\b",
            re.I | re.S,
        ),
        "prompt injection: override-guidelines",
    ),
    (
        re.compile(
            r"(decode|decrypt|de-?obfuscate)\s+.{0,30}?\b(and|then)\s+(follow|execute|run|obey|do)\b",
            re.I | re.S,
        ),
        "prompt injection: decode-then-follow",
    ),
]

# Deliberately NOT patterns here, so this isn't re-litigated on the next pass:
# - Bare `^(system|assistant):` role prefixes — too common in legitimate meeting
#   minutes and transcripts flowing through `context=`.
# - Markdown-image exfil (`![](http://attacker/?q=...)`) — the classic indirect-exfil
#   channel, but legitimate documents carry markdown images; a fail-closed block here
#   would be a false-positive factory.
# - `### Instruction` (Alpaca-style) — a plausible real markdown heading.

# 80+ chars of pure base64 alphabet in one unbroken run — heuristic for an encoded
# payload smuggled in as "harmless" text. Runs against the RAW text, not the canonical
# one: see scan().
_BASE64_RUN_RE = re.compile(r"[A-Za-z0-9+/]{80,}")


def _canon(text: str) -> str:
    """Fold text to lowercase ASCII, so the patterns see one spelling per phrase.

    Four passes, in this order:

    1. Strip invisibles — a zero-width joiner splitting a word ("ig<ZWNJ>nore").
    2. Repair mojibake — UTF-8 read as Latin-1 ("Ã¯gnore" back to "ïgnore"), which
       otherwise transliterates to noise in pass 4 and misses.
    3. NFKC — fullwidth and other compatibility forms.
    4. Transliterate — a Cyrillic or Greek letter standing in for a Latin one.

    Passes 3 and 4 overlap but are not redundant: 343 codepoints canonicalize
    differently with NFKC than without it, in both directions, so both stay.
    """
    kept = "".join(c for c in text if c in "\t\n\r" or unicodedata.category(c) not in _INVISIBLE_CATEGORIES)
    return anyascii(unicodedata.normalize("NFKC", fix_text(kept))).lower()


def scan(prompt: str, context: str | None = None) -> str | None:
    """Layer 1 prompt scan. Returns None if clean, or a block reason string.

    `context` and `prompt` are scanned as ONE joined string, never separately: the
    model receives them concatenated (see crema.api._user_content), so an injection
    split across the two fields — "…please ignore all" in context, "previous
    instructions…" in prompt — is only visible in the join. Each field remains a
    substring of the join, so this is strictly stronger than per-field scanning.

    The join deliberately uses a bare newline rather than the `<context>…</context>`
    wrapper crema.api sends: that wrapper is crema's own text, not attacker input, and a
    literal "</context>" sitting between the two halves would stop every pattern here
    from spanning the boundary — which is exactly what needs to be caught.

    Text is canonicalized by _canon (see there) before the pattern loop, but AFTER the
    raw-text unicode check below, so a fullwidth spelling, a word split by a zero-width
    joiner, a mojibake'd phrase, or a Cyrillic letter standing in for a Latin one all
    trip the ordinary ASCII patterns. The order matters: canonicalizing first would strip
    away the very codepoints _is_blocked_char exists to catch. Patterns added to
    _INJECTION_PATTERNS match the canonical text, so write them in lowercase ASCII.

    Known remaining gap: anyascii transliterates phonetically, it is not a TR39
    confusables skeleton. It folds the look-alikes whose transliteration happens to be
    the letter they resemble (Cyrillic a/ie/o/byelorussian-i/je, Greek alpha/nu/omicron/
    mu/tau) but not the ones where it diverges — Cyrillic es becomes "s" not "c", er
    becomes "r" not "p", ha becomes "kh" not "x", u becomes "u" not "y". So a "pretend
    you are" spelled with a Cyrillic er still evades. Closing that needs a confusables
    table, deliberately not added here.

    Pure function: no frappe imports, no I/O — the three third-party imports are static
    tables. Failure behavior = block, so any new check added here should default to
    returning a reason on ambiguity.
    """
    raw = "\n".join(part for part in (context, prompt) if part)
    if not raw:
        return None

    # Raw text, before _canon: _canon's own strip deletes exactly the bidi overrides and
    # format characters this check is here to fail closed on.
    if any(_is_blocked_char(c) for c in raw):
        return "blocked: disallowed unicode formatting/control character"

    text = _canon(raw)

    for pattern, reason in _INJECTION_PATTERNS:
        if pattern.search(text):
            return reason

    # Raw text too: anyascii romanizes CJK with no word separators, so ~100 unpunctuated
    # kanji become a 230-character [A-Za-z] run and trip this heuristic. Base64 payloads
    # are ASCII by definition, so reading raw loses nothing and costs no false positives.
    if _BASE64_RUN_RE.search(raw):
        return "possible encoded payload"

    return None
