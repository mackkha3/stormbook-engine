"""P0 reply classification: Branch 1 (slot picked) and Branch 3 (wrong number).

Branches 2, 4, 5 and 6 (counter-offer, call-me, questions, nonsense) are out
of scope — they land as UNMATCHED and Mack narrates them verbally on the call.

STOP / HELP / UNSUBSCRIBE are handled by Twilio at the platform level. We
deliberately do not touch them here.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

Branch = Literal["YES_SLOT_A", "YES_SLOT_B", "WRONG_NUMBER", "UNMATCHED"]


class BranchMatch(BaseModel):
    branch: Branch
    matched_keyword: str | None = None


WRONG_NUMBER_PHRASES = (
    "wrong number",
    "not me",
    "not mine",
    "who is this",
    "didn't request",
    "did not request",
    # Straight vs. curly apostrophe — iOS autocorrects to the curly one.
    "didn’t request",
)

# Word-boundary alternates per day. Substring matching would fire on
# "money" (mon), "sunny" (sun) and "wedding" (wed).
DAY_ALIASES: dict[str, tuple[str, ...]] = {
    "Monday": ("monday", "mon"),
    "Tuesday": ("tuesday", "tues", "tue"),
    "Wednesday": ("wednesday", "weds", "wed"),
    "Thursday": ("thursday", "thurs", "thur", "thu"),
    "Friday": ("friday", "fri"),
    "Saturday": ("saturday", "sat"),
    "Sunday": ("sunday", "sun"),
}


def _find_day(text: str, day: str) -> str | None:
    """Return the alias that matched, or None. Longest alias wins."""
    for alias in DAY_ALIASES.get(day, ()):
        if re.search(rf"\b{re.escape(alias)}\b", text):
            return alias
    return None


def classify(body: str, *, slot_a_day: str, slot_b_day: str) -> BranchMatch:
    """Classify a homeowner reply. Case-insensitive; order matters.

    1. WRONG_NUMBER wins outright — "wrong number, not Tuesday" is a close-out,
       not a booking.
    2. Then slot A, then slot B.
    3. If BOTH slot days appear ("either works"), we bail to UNMATCHED —
       P0 does not resolve ambiguity.
    """
    text = (body or "").lower()

    for phrase in WRONG_NUMBER_PHRASES:
        if phrase in text:
            return BranchMatch(branch="WRONG_NUMBER", matched_keyword=phrase)

    a_hit = _find_day(text, slot_a_day)
    b_hit = _find_day(text, slot_b_day)

    if a_hit and b_hit:
        # Ambiguous — logged by the caller for future Branch 2 work.
        return BranchMatch(branch="UNMATCHED", matched_keyword=None)
    if a_hit:
        return BranchMatch(branch="YES_SLOT_A", matched_keyword=a_hit)
    if b_hit:
        return BranchMatch(branch="YES_SLOT_B", matched_keyword=b_hit)

    return BranchMatch(branch="UNMATCHED", matched_keyword=None)


def is_ambiguous(body: str, *, slot_a_day: str, slot_b_day: str) -> bool:
    """True when both offered days appear in one reply. Used for logging."""
    text = (body or "").lower()
    return bool(_find_day(text, slot_a_day)) and bool(_find_day(text, slot_b_day))
