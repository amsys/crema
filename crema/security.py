"""Security scanning — layer 1 (regex/unicode) and layer 2 (LLM guard) glue.

Layer 1 (`scan`) is pure: no frappe imports, no I/O, just compiled regexes over the
prompt/context strings. Layer 2 (the LLM guard) is wired directly into
`crema.api.ask` via a recursive `ask("security", ...)` call — see the
`# phase 3: llm guard` seam there.
"""

from __future__ import annotations

import re
import unicodedata

# Unicode "invisible/control" characters to reject: every Cf (Format) category
# codepoint EXCEPT U+200E/U+200F (directional marks) and U+200C/U+200D (ZWNJ/ZWJ),
# which are legitimate in Arabic/Indic shaping and emoji ZWJ sequences.
#
# Hardcoded as \\u/\\U escapes (never written as literal invisible/bidi characters in
# this source file) because the Cf category is small (21 ranges) and stable for
# already-assigned codepoints. Regenerate with:
#   for cp in range(0x110000):
#       if unicodedata.category(chr(cp)) == "Cf" and cp not in {0x200c, 0x200d, 0x200e, 0x200f}: ...
#
# Includes the zero-width space (U+200B), the bidi override/isolate blocks
# (U+202A-U+202E, U+2066-U+2069), and the full deprecated tag block (U+E0000-U+E007F,
# widened beyond the strict Cf subset to cover the whole block per the security spec).
_UNICODE_BLOCK_RE = re.compile(
    "["
    "\u00ad"  # soft hyphen
    "\u0600-\u0605"  # Arabic number signs
    "\u061c"  # Arabic letter mark
    "\u06dd"  # Arabic end of ayah
    "\u070f"  # Syriac abbreviation mark
    "\u0890-\u0891"  # Arabic pound/piastre marks
    "\u08e2"  # Arabic disputed end of ayah
    "\u180e"  # Mongolian vowel separator
    "\u200b"  # zero-width space
    "\u202a-\u202e"  # bidi embedding/override
    "\u2060-\u2064"  # word joiner, invisible operators
    "\u2066-\u206f"  # bidi isolates (U+2066-U+2069) + deprecated format chars
    "\ufeff"  # BOM / zero-width no-break space
    "\ufff9-\ufffb"  # interlinear annotation marks
    "\U000110bd"  # Kaithi number sign
    "\U000110cd"  # Kaithi number sign above
    "\U00013430-\U0001343f"  # Egyptian hieroglyph format controls
    "\U0001bca0-\U0001bca3"  # shorthand format controls
    "\U0001d173-\U0001d17a"  # musical symbol format controls
    "\U000e0000-\U000e007f"  # deprecated tag block (widened beyond strict Cf)
    "]"
)

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
            r"(repeat|print|output|show|display)\s+.{0,20}?\b(everything|all\s+(of\s+)?the\s+text)\s+"
            r"(above|before|preceding)\b",
            re.I | re.S,
        ),
        "prompt injection: exfiltrate prior context",
    ),
    (
        re.compile(
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
# payload smuggled in as "harmless" text.
_BASE64_RUN_RE = re.compile(r"[A-Za-z0-9+/]{80,}")


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

    Text is NFKC-normalized before the pattern loop (but AFTER the raw-text unicode
    check below), so a fullwidth or other compatibility-form spelling of an attack
    phrase (see test_scan_catches_fullwidth_evasion) still trips the ordinary ASCII
    patterns below. The order matters: normalizing first would fold away some of the
    very codepoints _UNICODE_BLOCK_RE exists to catch. Patterns added to
    _INJECTION_PATTERNS match the normalized text. Known remaining gap: NFKC does not
    fold look-alike letters from other scripts (a Cyrillic letter standing in for a
    Latin one) — that needs a confusables table, deliberately not added here.

    Pure function: no frappe imports, no I/O. Failure behavior = block, so any new
    check added here should default to returning a reason on ambiguity.
    """
    text = "\n".join(part for part in (context, prompt) if part)
    if not text:
        return None

    if _UNICODE_BLOCK_RE.search(text):
        return "blocked: disallowed unicode formatting/control character"

    text = unicodedata.normalize("NFKC", text)

    for pattern, reason in _INJECTION_PATTERNS:
        if pattern.search(text):
            return reason

    if _BASE64_RUN_RE.search(text):
        return "possible encoded payload"

    return None
