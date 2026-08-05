"""Shared JSON-response helpers.

Its own module because both crema.api and crema._ocr need `strip_fence`, and crema._ocr is
imported BY crema.api — the same circular-import reason crema.log exists.
"""

from __future__ import annotations

import re

# Two patterns, not one alternation: S5850 wants the alternatives explicitly grouped,
# but grouping them non-capturing with no quantifier trips S6395 right back — there is
# no single regex that satisfies both. Splitting into an open/close pair sub'd in order
# does the same trim (leading fence, then trailing fence) and satisfies both rules.
_OPEN_FENCE_RE = re.compile(r"^```[\w-]*\n?")
_CLOSE_FENCE_RE = re.compile(r"\n?```$")


def strip_fence(text: str) -> str:
    """Strip a markdown code fence (```json ... ```) some models wrap JSON in."""
    return _CLOSE_FENCE_RE.sub("", _OPEN_FENCE_RE.sub("", text.strip())).strip()
