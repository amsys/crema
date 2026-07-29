"""Shared JSON-response helpers.

Its own module because both crema.api and crema._ocr need `strip_fence`, and crema._ocr is
imported BY crema.api — the same circular-import reason crema.log exists.
"""

from __future__ import annotations

import re

_FENCE_RE = re.compile(r"^```[\w-]*\n?|\n?```$")


def strip_fence(text: str) -> str:
    """Strip a markdown code fence (```json ... ```) some models wrap JSON in."""
    return _FENCE_RE.sub("", text.strip()).strip()
