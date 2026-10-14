"""Template rendering + the output safety check.

"The AI reads. The templates write." This module is where that is enforced:
the only way to produce a `RenderedMessage` the engine will send is
`Templates.render(key, ...)`, and `check_output` re-derives the text from the
template key before every send, so nothing else can reach a homeowner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from string import Formatter

import yaml

from booking_engine.settings import CONFIG_DIR

ALLOWED_VARIABLES = {"company_name", "booked_time", "service_area", "company_phone"}
_SLOT_VAR = re.compile(r"^slot_([1-9])$")

BANNED_PHRASES = (
    "deductible",
    "guarantee",
    "insurance will",
    "covered by insurance",
    "free roof",
    "waive",
)
_DOLLAR = re.compile(r"\$\s*\d|\b\d[\d,.]*\s*(?:dollars?|bucks|usd)\b", re.IGNORECASE)
# Anything that reads as a clock time or a calendar date.
_TIME_LIKE = re.compile(
    r"\b\d{1,2}:\d{2}\b"
    r"|\b\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?)(?![a-z])"
    r"|\b\d{1,2}/\d{1,2}\b"
    r"|\b(?:noon|midnight)\b"
    r"|\b(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b"
    r"|\b(?:mon|tues|wednes|thurs|fri|satur|sun)day\b"
    r"|\b(?:today|tonight|tomorrow)\b",
    re.IGNORECASE,
)


class TemplateError(Exception):
    """templates.yaml is malformed, or a render was asked for something it can't do."""


class UnsafeOutput(Exception):
    """A message failed the output safety check and must not be sent."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class RenderedMessage:
    key: str
    text: str
    variables: dict[str, str] = field(default_factory=dict)


def _capitalize(text: str) -> str:
    """A message can open with a variable ("{slot_1} is open"); it still starts a sentence."""
    return text[:1].upper() + text[1:]


def static_violation(text: str) -> str | None:
    """Dollar amounts and banned phrases — checks that need no calendar context."""
    if _DOLLAR.search(text):
        return "dollar_amount"
    lowered = text.lower()
    for phrase in BANNED_PHRASES:
        if phrase in lowered:
            return f"banned_phrase:{phrase}"
    return None


class Templates:
    def __init__(self, templates: dict[str, str]) -> None:
        self._templates = dict(templates)
        self._fields = {key: self._parse(key, text) for key, text in templates.items()}
        for key, text in templates.items():
            violation = static_violation(text) or (
                "hardcoded_time" if _TIME_LIKE.search(text) else None
            )
            if violation:
                raise TemplateError(f"template {key!r} breaks an output rule: {violation}")

    @classmethod
    def load(cls, path: Path | None = None) -> Templates:
        path = path or CONFIG_DIR / "templates.yaml"
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise TemplateError(f"cannot load {path}: {exc}") from exc
        if not isinstance(raw, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in raw.items()
        ):
            raise TemplateError(f"{path.name}: expected `key: \"text\"` pairs")
        return cls(raw)

    @staticmethod
    def _parse(key: str, text: str) -> set[str]:
        try:
            names = {name for _, name, _, _ in Formatter().parse(text) if name is not None}
        except ValueError as exc:
            raise TemplateError(f"template {key!r}: bad braces: {exc}") from exc
        unknown = {n for n in names if n not in ALLOWED_VARIABLES and not _SLOT_VAR.match(n)}
        if unknown:
            raise TemplateError(f"template {key!r} uses unknown variables: {sorted(unknown)}")
        return names

    def has(self, key: str) -> bool:
        return key in self._templates

    def require(self, keys: set[str]) -> None:
        missing = sorted(keys - set(self._templates))
        if missing:
            raise TemplateError(f"templates.yaml is missing required templates: {missing}")

    def slot_count(self, key: str) -> int:
        """How many {slot_N} times this template shows (highest N used)."""
        if key not in self._fields:
            raise TemplateError(f"unknown template key {key!r}")
        numbers = [int(m.group(1)) for n in self._fields[key] if (m := _SLOT_VAR.match(n))]
        return max(numbers, default=0)

    def render(self, key: str, variables: dict[str, str]) -> RenderedMessage:
        if key not in self._templates:
            raise TemplateError(f"unknown template key {key!r}")
        needed = self._fields[key]
        missing = sorted(n for n in needed if not variables.get(n))
        if missing:
            raise TemplateError(f"template {key!r} is missing values for {missing}")
        used = {n: str(variables[n]) for n in needed}
        return RenderedMessage(
            key=key, text=_capitalize(self._templates[key].format(**used)), variables=used
        )

    def check_output(self, message: RenderedMessage, allowed_times: list[str]) -> None:
        """Raise UnsafeOutput unless `message` is safe to send.

        `allowed_times` are the formatted slots the calendar returned this turn.
        """
        if not isinstance(message, RenderedMessage) or message.key not in self._templates:
            raise UnsafeOutput("not_from_template")
        try:
            expected = self.render(message.key, message.variables)
        except TemplateError as exc:
            raise UnsafeOutput("not_from_template") from exc
        if expected.text != message.text:
            raise UnsafeOutput("not_from_template")

        violation = static_violation(message.text)
        if violation:
            raise UnsafeOutput(violation)

        for name, value in message.variables.items():
            if (_SLOT_VAR.match(name) or name == "booked_time") and value not in allowed_times:
                raise UnsafeOutput("time_not_from_calendar")
        # What's left after removing the variable values is the template's own
        # wording, which must not carry a date or time of its own.
        remainder = message.text
        for value in sorted(set(message.variables.values()), key=len, reverse=True):
            # A value that opens the message was capitalised ("Today at 3 PM is open").
            remainder = remainder.replace(value, " ").replace(_capitalize(value), " ")
        if _TIME_LIKE.search(remainder):
            raise UnsafeOutput("time_not_from_calendar")
