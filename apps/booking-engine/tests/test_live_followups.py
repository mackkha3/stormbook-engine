"""Live model checks (pytest -m live) for replies to the newer follow-up texts.

Each context is the conversation exactly as the homeowner read it, with the
real templates, so the model is judged on the wording actually sent.
"""

from __future__ import annotations

import datetime as dt

import pytest

from booking_engine.settings import Settings
from booking_engine.slots import friendly_slot
from booking_engine.templates import Templates
from booking_engine.understanding import (
    AnthropicUnderstander,
    TurnContext,
    Understanding,
    apply_confidence_rule,
    apply_preference_rule,
)
from tests.conftest import NOW

TZ = NOW.tzinfo
TEMPLATES = Templates.load()
OPENING = TEMPLATES.render("opening_missed_call", {"company_name": "Roof Demo"}).text


async def understand(ctx: TurnContext) -> tuple[Understanding, Understanding]:
    """(what the model said, what the engine acts on)."""
    api_key = Settings.from_env().anthropic_api_key
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set (put it in apps/booking-engine/.env)")
    async with AnthropicUnderstander(api_key) as understander:
        raw = await understander.understand(ctx)
    return raw, apply_preference_rule(apply_confidence_rule(raw))


# --- "you're still on for..." after a new opener ----------------------------------

REMINDER_NOW = dt.datetime(2026, 10, 13, 19, 0, tzinfo=TZ)          # Tuesday evening
BOOKED = dt.datetime(2026, 10, 14, 8, 0, tzinfo=TZ)                  # Wednesday 8 AM


def _reminder_context(text: str) -> TurnContext:
    reminder = TEMPLATES.render(
        "still_booked", {"booked_time": friendly_slot(BOOKED, TZ, REMINDER_NOW)}
    ).text
    return TurnContext(
        message=text, now=REMINDER_NOW, status="booked", offered=[], booked_time=BOOKED,
        history=[
            {"role": "engine", "text": OPENING},
            {"role": "homeowner", "text": "Yes"},
            {"role": "engine", "text": reminder},
        ],
    )


def test_reminder_context_is_the_text_the_homeowner_read():
    assert _reminder_context("x").history[-1]["text"] == (
        "Hey, you're still on for tomorrow at 8 AM. Want to keep that or move it?"
    )


@pytest.mark.live
@pytest.mark.parametrize(
    "text", ["keep it", "that's fine", "yep still good", "keep it thanks", "Keep it"]
)
async def test_live_keeping_the_appointment_is_an_affirm(text):
    raw, u = await understand(_reminder_context(text))
    assert u.intent == "affirm", f"model said {raw.intent} ({raw.confidence}): {raw.notes}"


@pytest.mark.live
@pytest.mark.parametrize("text", ["move it", "can we do friday instead", "need a different day"])
async def test_live_moving_it_is_a_reschedule(text):
    raw, u = await understand(_reminder_context(text))
    assert u.intent in ("reschedule", "propose_time"), (
        f"model said {raw.intent} ({raw.confidence}): {raw.notes}"
    )


# --- an offer worded "today at ... or tomorrow at ..." -----------------------------

OFFER_NOW = dt.datetime(2026, 10, 13, 10, 0, tzinfo=TZ)              # Tuesday morning
TODAY_3 = dt.datetime(2026, 10, 13, 15, 0, tzinfo=TZ)
TOMORROW_9 = dt.datetime(2026, 10, 14, 9, 0, tzinfo=TZ)
TODAY_TOMORROW_PICKS = [
    ("today works", 1), ("tomorrow", 2), ("the morning one", 2), ("tomorrow morning is better", 2),
    ("this afternoon", 1), ("today at 3 is good", 1), ("tmrw", 2), ("the 3 o'clock", 1),
]


def _today_tomorrow_context(text: str) -> TurnContext:
    offer = TEMPLATES.render("offer_slots", {
        "slot_1": friendly_slot(TODAY_3, TZ, OFFER_NOW),
        "slot_2": friendly_slot(TOMORROW_9, TZ, OFFER_NOW),
    }).text
    return TurnContext(
        message=text, now=OFFER_NOW, status="slots_offered", offered=[TODAY_3, TOMORROW_9],
        history=[
            {"role": "engine", "text": OPENING},
            {"role": "homeowner", "text": "yes we had hail"},
            {"role": "engine", "text": offer},
        ],
    )


def test_today_tomorrow_context_is_the_text_the_homeowner_read():
    assert _today_tomorrow_context("x").history[-1]["text"] == (
        "Great, I've got today at 3 PM or tomorrow at 9 AM. Which works better?"
    )


@pytest.mark.live
@pytest.mark.parametrize("text, choice", TODAY_TOMORROW_PICKS)
async def test_live_today_or_tomorrow_answers_pick_the_right_slot(text, choice):
    raw, u = await understand(_today_tomorrow_context(text))
    assert (u.intent, u.slot_choice) == ("pick_slot", choice), (
        f"model said {raw.intent} slot {raw.slot_choice} ({raw.confidence}): {raw.notes}"
    )
