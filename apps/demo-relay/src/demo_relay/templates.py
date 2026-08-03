"""SMS copy for the phone-in-my-hand demo.

Four messages, all P0:

  Message 1  — the opener, fired by POST /fire
  Message 2  — 10-minute nudge, fired only if the homeowner stayed silent
  Message 3A — slot confirmed (Branch 1)
  Message 3D — wrong number (Branch 3)

Rendering is strict on purpose: a missing field raises MissingTemplateField
rather than emitting a literal "{homeowner_first_name}" onto a prospect's
phone mid-demo.
"""

from __future__ import annotations

from typing import Any

MESSAGE_1 = (
    "Hi {homeowner_first_name}, this is {contractor_first_name} with "
    "{contractor_company}. We just received your inspection request for "
    "{homeowner_address} about the {damage_type_lowercased} damage.\n\n"
    "We can come by {slot_a_day} at {slot_a_time} or {slot_b_day} at "
    "{slot_b_time} for a free roof assessment. It takes about 30 minutes, "
    "no obligation.\n\n"
    "Which day works better?"
)

MESSAGE_2 = (
    "{homeowner_first_name} — {contractor_first_name} here again. If "
    "{slot_a_day} at {slot_a_time} or {slot_b_day} at {slot_b_time} don't "
    "work, no problem — reply with a better time and I'll check our schedule."
)

MESSAGE_3A = (
    "Perfect. You're booked for {chosen_day} at {chosen_time}, "
    "{homeowner_address}. If anything changes, just text me here.\n\n"
    "— {contractor_first_name}, {contractor_company}"
)

MESSAGE_3D = (
    "No problem — sorry for the confusion. We'll close this out. Have a "
    "good one.\n\n"
    "— {contractor_first_name}, {contractor_company}"
)


class MissingTemplateField(KeyError):
    """A template placeholder had no value in the supplied context."""

    def __init__(self, field: str, template_name: str) -> None:
        super().__init__(field)
        self.field = field
        self.template_name = template_name

    def __str__(self) -> str:
        return f"missing field {self.field!r} for template {self.template_name!r}"


class _StrictContext(dict):
    def __init__(self, data: dict[str, Any], template_name: str) -> None:
        super().__init__(data)
        self._template_name = template_name

    def __missing__(self, key: str) -> str:
        raise MissingTemplateField(key, self._template_name)


def _render(template: str, name: str, context: dict[str, Any]) -> str:
    # Drop Nones/empties so they surface as MissingTemplateField rather than "None".
    cleaned = {k: v for k, v in context.items() if v is not None and v != ""}
    return template.format_map(_StrictContext(cleaned, name))


def render_message_1(context: dict[str, Any]) -> str:
    """Opener. Expects the FireRequest fields plus `damage_type`."""
    ctx = dict(context)
    damage = ctx.get("damage_type")
    if isinstance(damage, str) and damage:
        ctx.setdefault("damage_type_lowercased", damage.lower())
    return _render(MESSAGE_1, "message_1", ctx)


def render_message_2(context: dict[str, Any]) -> str:
    """10-minute nudge."""
    return _render(MESSAGE_2, "message_2", context)


def render_message_3a(context: dict[str, Any], *, chosen_day: str, chosen_time: str) -> str:
    """Slot confirmation. `chosen_day`/`chosen_time` come from the matched branch."""
    ctx = dict(context)
    ctx["chosen_day"] = chosen_day
    ctx["chosen_time"] = chosen_time
    return _render(MESSAGE_3A, "message_3a", ctx)


def render_message_3d(context: dict[str, Any]) -> str:
    """Wrong-number close-out."""
    return _render(MESSAGE_3D, "message_3d", context)
