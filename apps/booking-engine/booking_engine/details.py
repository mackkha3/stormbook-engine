"""The homeowner's address and name: checked before they go anywhere.

These are the only words of the homeowner's own that the engine ever texts back
("You're all set for ... at {address}. Our inspector will ask for {name}."), so
both are held to a strict shape. A value that fails is never texted or written
to the contact; the raw text goes into a note for the roofer instead.
"""

from __future__ import annotations

import re

from booking_engine.templates import static_violation, time_like

ADDRESS_MIN, ADDRESS_MAX = 5, 100
NAME_MAX = 40

# Street types, plus the Texas rural forms: FM / Farm to Market Road, CR / County
# Road, Hwy / Highway, RR (ranch or rural road), Loop, Spur, PR (private or park road),
# and numbered US / TX / SH highways ("US 69", but not "2 of us").
_STREET_WORDS = re.compile(
    r"\b(?:st|street|rd|road|dr|drive|ln|lane|ave|avenue|blvd|boulevard|ct|court|cir|circle"
    r"|way|pl|place|pkwy|parkway|trl|trail|ter|terrace|cv|cove|pt|point|xing|crossing"
    r"|fm|cr|county\s+road|farm\s+to\s+market(?:\s+road)?|hwy|highway|rr|loop|spur|pr"
    r"|state\s+highway|(?:us|sh|tx)(?=\s*\d))\b",
    re.IGNORECASE,
)
_ADDRESS_CHARS = re.compile(r"^[A-Za-z0-9 .,#'\-]+$")
_LETTERS = r"[^\W\d_]+"                                  # letters, accented ones included
_NAME_WORD = re.compile(rf"^{_LETTERS}(?:['’\-]{_LETTERS})*\.?$")
_JOINERS = {"and", "&"}


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().strip(",").strip()


def clean_address(raw: str | None) -> str | None:
    """The address as it may be texted back and saved, or None if it can't be trusted.

    Needs a number and a street word ("12345 CR 4120", "FM 2493 Tyler", "Hwy 69 N",
    "Loop 323"), only ordinary address characters (no links, emails or slashes), and
    nothing the output rules ban, including anything that reads as a date or time (it
    would be texted as if the calendar said it).
    """
    if not raw:
        return None
    address = _squash(raw)
    if not ADDRESS_MIN <= len(address) <= ADDRESS_MAX or not _ADDRESS_CHARS.match(address):
        return None
    if not re.search(r"\d", address) or not _STREET_WORDS.search(address):
        return None
    return None if static_violation(address) or time_like(address) else address


def clean_name(raw: str | None) -> str | None:
    """Who the inspector should ask for, or None. Letters (accented too), hyphens,
    apostrophes and spaces, joined by "and" / "&" ("Chris and Amy"); no digits or links."""
    if not raw:
        return None
    name = _squash(raw)
    if not 0 < len(name) <= NAME_MAX or static_violation(name) or time_like(name):
        return None
    words = name.split(" ")
    if words[0].lower() in _JOINERS or words[-1].lower() in _JOINERS:
        return None
    if all(w.lower() in _JOINERS or _NAME_WORD.match(w) for w in words):
        return name
    return None


def split_name(name: str) -> tuple[str, str]:
    """(first, last) for the contact. A couple ("Chris and Amy") stays whole as the first name."""
    words = name.split(" ")
    if len(words) == 1 or any(w.lower() in _JOINERS for w in words):
        return name, ""
    return " ".join(words[:-1]), words[-1]
