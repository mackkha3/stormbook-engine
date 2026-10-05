"""Conversation logic: every intent → the right action, plus the code-enforced rules."""

from __future__ import annotations

import datetime as dt

import pytest

from booking_engine.gateway import TAG_ACTIVE, TAG_EMERGENCY, TAG_HANDOFF
from booking_engine.understanding import UnderstandingError
from tests.conftest import FIXTURES, Harness, U


@pytest.mark.parametrize("fixture", FIXTURES, ids=[f["text"][:40] for f in FIXTURES])
async def test_fixture_message_gets_the_expected_action(fixture):
    h = Harness()
    await h.reach(fixture["state"])
    understanding = U(
        fixture["intent"],
        slot_choice=fixture.get("slot_choice"),
        preferred_date=fixture.get("preferred_date") and dt.date.fromisoformat(fixture["preferred_date"]),
        preferred_time_window=fixture.get("preferred_time_window"),
    )
    result = await h.say(fixture["text"], understanding)
    assert (result.template_key or "none") == fixture["expect"]
    assert len(h.gateway.sent) == (0 if fixture["expect"] == "none" else 1)   # one reply per turn


async def test_low_confidence_is_treated_as_unclear(h: Harness):
    result = await h.say("uhh maybe", U("affirm", confidence=0.69))
    assert (result.intent, result.template_key) == ("unclear", "clarify")


async def test_second_unclear_in_a_row_hands_off(h: Harness):
    assert (await h.say("?", U("unclear"))).template_key == "clarify"
    result = await h.say("??", U("unclear"))
    assert (result.template_key, result.handoff_reason) == ("handoff", "unclear_twice")
    assert h.conv.status == "handoff"


async def test_unclear_streak_resets_after_a_clear_message(h: Harness):
    await h.say("?", U("unclear"))
    await h.say("how much", U("deferrable_question"))
    assert (await h.say("hm", U("unclear"))).template_key == "clarify"


async def test_non_english_hands_off(h: Harness):
    result = await h.say("sí, por favor", U("affirm", language="es"))
    assert result.template_key == "handoff" and result.handoff_reason == "non_english:es"


async def test_opt_out_keyword_wins_even_if_the_model_says_otherwise(h: Harness):
    await h.reach("offered")
    h.understander.queue.append(U("pick_slot", slot_choice=2))
    result = await h.engine.handle_turn(
        h.contact, [h.message("yes tuesday works"), h.message("actually STOP")]
    )
    assert result.intent == "opt_out" and result.reply is None
    assert h.conv.status == "opted_out" and not h.calendar.appointments
    assert h.understander.queue      # the model was never even asked


async def test_model_opt_out_wins_over_low_confidence_and_language(h: Harness):
    result = await h.say("no mas mensajes", U("opt_out", confidence=0.4, language="es"))
    assert result.reply is None and h.conv.status == "opted_out" and not h.gateway.calls


async def test_model_failure_hands_off_instead_of_guessing(h: Harness):
    result = await h.say("yes", UnderstandingError("invalid JSON twice"))
    assert (result.template_key, result.handoff_reason) == ("handoff", "understanding_failed")


async def test_handoff_tags_and_note_with_last_six_messages(h: Harness):
    await h.reach("offered")
    for i in range(3):
        await h.say(f"question {i}", U("deferrable_question"))
    h.gateway.calls.clear()
    await h.say("can someone call me", U("human_request"))
    calls = {c["call"]: c for c in h.gateway.calls}
    assert calls["add_tags"]["tags"] == [TAG_HANDOFF]
    assert calls["remove_tags"]["tags"] == [TAG_ACTIVE]
    note = calls["add_note"]["body"]
    assert "Reason: human_request" in note
    lines = note.split("\n\n", 1)[1].splitlines()
    assert len(lines) == 6
    assert lines[-2] == "homeowner: can someone call me" and lines[-1].startswith("engine: Thanks!")


async def test_emergency_is_an_urgent_handoff(h: Harness):
    result = await h.say("water is coming in", U("emergency"))
    assert result.template_key == "emergency" and h.client.company_phone in result.reply
    assert h.gateway.tags_added() == [TAG_HANDOFF, TAG_EMERGENCY]


async def test_hostile_sends_nothing_but_still_hands_off(h: Harness):
    result = await h.say("f*** off", U("hostile"))
    assert result.reply is None and not h.gateway.sent
    assert h.conv.status == "handoff" and TAG_HANDOFF in h.gateway.tags_added()


@pytest.mark.parametrize(
    "stopper", [U("human_request"), U("opt_out"), U("not_interested"), U("hostile")]
)
async def test_engine_stays_silent_after_handoff_optout_or_close(h: Harness, stopper):
    await h.say("...", stopper)
    sent_before = len(h.gateway.sent)
    result = await h.say("actually yes book me", U("affirm"))
    assert result.ignored and result.reply is None and len(h.gateway.sent) == sent_before


async def test_stop_after_handoff_is_still_recorded_as_an_opt_out(h: Harness):
    await h.say("call me", U("human_request"))
    await h.say("STOP")
    assert h.conv.status == "opted_out"


async def test_second_none_work_asks_for_their_preference_then_treats_answer_as_a_proposal(h: Harness):
    await h.reach("offered")
    assert (await h.say("neither", U("none_work"))).template_key == "offer_more_slots"
    assert (await h.say("nope", U("none_work"))).template_key == "ask_preference"
    assert h.conv.awaiting_preference and h.conv.current_offer == []
    # Whatever the model calls it, a named day after ask_preference is a proposal.
    result = await h.say("thursday", U("unclear", preferred_date=dt.date(2026, 10, 8)))
    assert result.template_key == "propose_time_match" and "Thu 10/8" in result.reply


async def test_yes_to_lock_that_in_books_the_proposed_time(h: Harness):
    proposed = await h.say("saturday morning?", U(
        "propose_time", preferred_date=dt.date(2026, 10, 10), preferred_time_window="morning"))
    assert proposed.template_key == "propose_time_match"
    result = await h.say("yes", U("affirm"))
    assert result.template_key == "booked" and "Sat 10/10 at 9:00 AM" in result.reply


async def test_bare_yes_to_two_options_asks_which_one(h: Harness):
    await h.reach("offered")
    result = await h.say("yes", U("affirm"))
    assert result.template_key == "clarify" and not h.calendar.appointments
    assert "Mon 10/5 at 11:00 AM" in result.reply and "Tue 10/6 at 12:00 PM" in result.reply


async def test_max_eight_messages_without_booking_then_handoff(h: Harness):
    for _ in range(8):
        assert (await h.say("how much", U("deferrable_question"))).template_key == "deflect"
    result = await h.say("but how much", U("deferrable_question"))
    assert (result.template_key, result.handoff_reason) == ("handoff", "max_messages_without_booking")


async def test_a_booking_on_the_ninth_message_still_goes_through(h: Harness):
    for _ in range(8):
        await h.say("how much", U("deferrable_question"))
    result = await h.say("fine, 1", U("pick_slot", slot_choice=1))
    assert result.template_key == "booked" and h.conv.status == "booked"


async def test_questions_after_booking_go_to_a_human(h: Harness):
    await h.reach("booked")
    result = await h.say("how much will it cost", U("deferrable_question"))
    assert result.template_key == "handoff"
    assert result.handoff_reason == "deferrable_question_after_booking"


async def test_every_turn_is_stored_for_review(h: Harness):
    await h.say("yes", U("affirm"))
    await h.say("1", U("pick_slot", slot_choice=1))
    first, second = h.store.turns(h.conv.id)
    assert (first.inbound_text, first.intent, first.template_key) == ("yes", "affirm", "offer_slots")
    assert first.model_output["confidence"] == 0.95 and first.outbound_text.startswith("Great!")
    assert second.booking_result["action"] == "booked" and second.status_after == "booked"
    assert [c["call"] for c in second.crm_calls] == ["send_sms", "set_stage", "add_tags"]


async def test_unexpected_crash_hands_off_rather_than_going_quiet(h: Harness, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("database on fire")

    monkeypatch.setattr("booking_engine.engine.decide", boom)
    result = await h.say("yes", U("affirm"))
    assert result.reply is None and h.conv.status == "handoff"
    assert h.conv.handoff_reason == "internal_error: RuntimeError"
