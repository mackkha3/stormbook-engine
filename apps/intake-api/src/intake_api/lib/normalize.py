"""Lightweight defensive normalization.

The intake form already sends E.164, but we treat its output as input,
not authority. Meta and Angi webhooks send phone numbers in arbitrary
shapes — this is the single normalization path.
"""

from __future__ import annotations

import re

_DIGITS_RE = re.compile(r"\D+")


def normalize_phone(raw: str | None) -> str | None:
    """Return E.164 US phone (`+1XXXXXXXXXX`) or None if unparseable."""
    if not raw:
        return None
    digits = _DIGITS_RE.sub("", raw)
    if len(digits) == 11 and digits[0] == "1":
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    # Already in E.164? Accept if it starts with + and is plausibly long enough.
    stripped = raw.strip()
    if stripped.startswith("+") and 8 <= len(_DIGITS_RE.sub("", stripped)) <= 15:
        return "+" + _DIGITS_RE.sub("", stripped)
    return None


def normalize_address(raw: str | None) -> str:
    """Collapse whitespace and trim. Address geocoding handles the rest."""
    if not raw:
        return ""
    return re.sub(r"\s+", " ", raw).strip()
