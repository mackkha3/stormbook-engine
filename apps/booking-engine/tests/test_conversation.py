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
        question_topic=fixture.get("question_topic"),
        acknowledgment_only=fixture.get("acknowledgment_only", False),
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
    assert result.reply is None and h.conv.status == "opted_out" and not h.gateway.sent


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
    assert result.template_key == "which_one" and not h.calendar.appointments
    assert result.reply == (
        "Great! Which works better: Mon 10/5 at 11:00 AM or Tue 10/6 at 12:00 PM?"
    )
    assert h.conv.unclear_streak == 0 and h.conv.status == "slots_offered"
    # Same two slots stay on the table, so "2" still books the second one.
    booked = await h.say("2", U("pick_slot", slot_choice=2))
    assert booked.template_key == "booked" and "Tue 10/6 at 12:00 PM" in booked.reply


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


# --- opt-outs: carrier keywords, tags, booked appointments ---------------------

CARRIER_KEYWORDS = ["STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "OPTOUT", "REVOKE"]


@pytest.mark.parametrize("keyword", CARRIER_KEYWORDS)
@pytest.mark.parametrize("state", ["fresh", "offered", "asked", "booked"])
async def test_carrier_keyword_is_an_opt_out_in_every_state(state, keyword):
    h = Harness()
    await h.reach(state)
    asked_before = len(h.understander.contexts)
    result = await h.say(f" {keyword.lower()} ")
    assert result.intent == "opt_out" and result.reply is None and not h.gateway.sent
    assert h.conv.status == "opted_out"
    assert "engine-opted-out" in h.gateway.tags_added()
    assert len(h.understander.contexts) == asked_before      # decided in code; model not asked


@pytest.mark.parametrize("stopper", [U("human_request"), U("not_interested"), U("hostile")])
async def test_carrier_keyword_after_handoff_or_close_still_opts_out_and_tags(h: Harness, stopper):
    await h.say("...", stopper)
    h.gateway.calls.clear()
    result = await h.say("Unsubscribe")
    assert not result.ignored and result.reply is None
    assert h.conv.status == "opted_out" and h.gateway.tags_added() == ["engine-opted-out"]
    # Already opted out: nothing further happens.
    h.gateway.calls.clear()
    assert (await h.say("STOP")).ignored and not h.gateway.calls


async def test_cancel_keyword_when_booked_opts_out_but_keeps_the_appointment(h: Harness):
    await h.reach("booked")
    result = await h.say("CANCEL")
    (appointment,) = h.calendar.appointments.values()
    assert not appointment.cancelled                       # the carrier blocked us; the job stands
    assert result.reply is None and not h.gateway.sent and result.booking is None
    conv = h.conv
    assert conv.status == "opted_out" and conv.appointment_id == appointment.id
    assert h.gateway.tags_added() == ["engine-opted-out", "engine-optout-booked"]
    (note,) = h.gateway.notes()
    assert "can no longer be texted" in note and "Mon 10/5 at 11:00 AM is STILL BOOKED" in note
    assert len(h.understander.contexts) == 2      # only the two set-up turns reached the model


async def test_opt_out_without_a_booking_gets_no_booked_tag_or_note(h: Harness):
    await h.reach("offered")
    await h.say("stop")
    assert h.gateway.tags_added() == ["engine-opted-out"] and not h.gateway.notes()


async def test_multi_word_cancel_stays_the_cancel_intent(h: Harness):
    await h.reach("booked")
    result = await h.say("can we cancel tuesday", U("cancel"))
    assert (result.intent, result.template_key) == ("cancel", "cancelled")
    assert h.conv.status == "cancelled" and "engine-opted-out" not in h.gateway.tags_added()
    assert next(iter(h.calendar.appointments.values())).cancelled


async def test_model_detected_opt_out_adds_the_opted_out_tag(h: Harness):
    result = await h.say("quit blowing up my phone with these", U("opt_out"))
    assert result.reply is None and h.conv.status == "opted_out"
    assert [c["call"] for c in h.gateway.calls] == ["add_tags"]
    assert h.gateway.tags_added() == ["engine-opted-out"]


async def test_model_detected_opt_out_when_booked_warns_the_roofer(h: Harness):
    await h.reach("booked")
    await h.say("take my number off this", U("opt_out"))
    assert h.gateway.tags_added() == ["engine-opted-out", "engine-optout-booked"]
    assert len(h.gateway.notes()) == 1 and not next(iter(h.calendar.appointments.values())).cancelled


async def test_opt_out_after_a_cancelled_booking_is_not_flagged_as_booked(h: Harness):
    await h.reach("booked")
    await h.say("please cancel my inspection", U("cancel"))
    h.gateway.calls.clear()
    await h.say("stop")
    assert h.gateway.tags_added() == ["engine-opted-out"] and not h.gateway.notes()


# --- question_topic picks the basic answer ------------------------------------


@pytest.mark.parametrize(
    "text, topic, expected",
    [
        ("do yall come out to Whitehouse", "area", "basic_area"),
        ("who is this", "who", "basic_who"),
        # Wording is irrelevant: only the model's question_topic decides.
        ("what company is this and where are you located", "who", "basic_who"),
        ("r u guys around bullard", "area", "basic_area"),
        ("what are your hours", "other", "deflect"),
        ("are you licensed in my area", "other", "deflect"),
        ("do you cover my area", None, "basic_who"),
    ],
)
async def test_question_topic_chooses_basic_who_or_basic_area(h: Harness, text, topic, expected):
    result = await h.say(text, U("basic_question", question_topic=topic))
    assert result.template_key == expected


async def test_other_topic_defers_to_the_inspector_with_two_slots(h: Harness):
    result = await h.say("what are your hours", U("basic_question", question_topic="other"))
    assert result.reply == (
        "Good question. Your inspector will go over that with you on-site, since it depends on "
        "what they find. Does Mon 10/5 at 11:00 AM or Tue 10/6 at 12:00 PM work better?"
    )
    assert len(h.conv.current_offer) == 2


# --- "stop" used normally must reach the model, not opt the homeowner out ------


@pytest.mark.parametrize(
    "text, understanding, expected",
    [
        ("can you stop by tomorrow?", U("propose_time", preferred_date=dt.date(2026, 10, 6)),
         "propose_time_match"),
        ("could someone stop by saturday", U("propose_time", preferred_date=dt.date(2026, 10, 10)),
         "propose_time_match"),
        ("water won't stop coming in", U("emergency"), "emergency"),
        ("WATER WONT STOP COMING IN", U("emergency"), "emergency"),
        ("the leak stopped but there's damage", U("deferrable_question"), "deflect"),
    ],
)
async def test_normal_uses_of_stop_do_not_opt_out(h: Harness, text, understanding, expected):
    result = await h.say(text, understanding)
    assert result.template_key == expected and result.intent == understanding.intent
    assert h.conv.status != "opted_out" and "engine-opted-out" not in h.gateway.tags_added()
    assert h.understander.contexts[-1].message == text          # the model was asked


async def test_water_wont_stop_is_an_urgent_emergency_handoff(h: Harness):
    result = await h.say("water won't stop coming in", U("emergency"))
    assert result.handoff_reason == "emergency" and h.client.company_phone in result.reply
    assert h.gateway.tags_added() == ["engine-handoff", "engine-emergency"]


# --- after a cancellation ---------------------------------------------------------


@pytest.mark.parametrize("text", ["ok", "thanks", "ok thx", "\U0001F44D"])
async def test_acknowledgment_after_cancellation_gets_no_reply(h: Harness, text):
    await h.reach("cancelled")
    result = await h.say(text, U("affirm", acknowledgment_only=True))
    assert result.reply is None and not h.gateway.calls and result.handoff_reason is None
    assert h.conv.status == "cancelled" and not result.ignored
    # ...and they can still come back afterwards.
    assert (await h.say("actually yes", U("affirm"))).template_key == "offer_slots"


async def test_acknowledgment_flag_does_not_swallow_a_real_request_after_cancellation(h: Harness):
    await h.reach("cancelled")
    result = await h.say("ok thanks, how much was it going to be",
                         U("deferrable_question", acknowledgment_only=True))
    assert result.template_key == "handoff"


@pytest.mark.parametrize(
    "understanding, expected",
    [
        (U("affirm"), "offer_slots"),
        (U("propose_time", preferred_date=dt.date(2026, 10, 9)), "propose_time_match"),
        (U("pick_slot", preferred_date=dt.date(2026, 10, 9), preferred_time_window="morning"),
         "propose_time_match"),
        (U("none_work"), "offer_more_slots"),
    ],
)
async def test_wanting_to_schedule_after_cancellation_reopens_with_fresh_slots(
    h: Harness, understanding, expected
):
    await h.reach("cancelled")
    previously_offered = set(h.conv.offered_slots)
    result = await h.say("changed my mind", understanding)
    conv = h.conv
    assert result.template_key == expected and result.handoff_reason is None
    assert conv.status == "slots_offered" and len(conv.current_offer) == 2
    if expected != "propose_time_match":       # their own named day may match an old offer
        assert not previously_offered & set(conv.current_offer)
    assert {"call": "remove_tags", "contact_id": "contact-1", "tags": ["engine-cancelled"]} in (
        h.gateway.calls)


async def test_reopened_conversation_books_a_new_appointment(h: Harness):
    await h.reach("cancelled")
    await h.say("actually yes", U("affirm"))
    result = await h.say("1", U("pick_slot", slot_choice=1))
    first, second = h.calendar.appointments.values()
    assert first.cancelled and not second.cancelled and first.id != second.id
    assert result.template_key == "booked" and result.booking["action"] == "booked"
    assert h.conv.status == "booked" and h.conv.appointment_id == second.id


async def test_reopening_restarts_the_message_cap(h: Harness):
    for _ in range(6):
        await h.say("how much", U("deferrable_question"))
    await h.say("1", U("pick_slot", slot_choice=1))
    await h.say("please cancel my inspection", U("cancel"))
    assert (await h.say("actually yes", U("affirm"))).template_key == "offer_slots"
    assert h.conv.engine_message_count == 1


@pytest.mark.parametrize(
    "understanding, reason",
    [
        (U("deferrable_question"), "deferrable_question_after_cancellation"),
        (U("basic_question", question_topic="who"), "basic_question_after_cancellation"),
        (U("is_bot"), "is_bot_after_cancellation"),
        (U("reschedule"), "reschedule_after_cancellation"),
        (U("cancel"), "cancel_after_cancellation"),
        (U("not_interested"), "not_interested_after_cancellation"),
        (U("unclear"), "unclear_after_cancellation"),
        (U("affirm", confidence=0.5), "unclear_after_cancellation"),
        (U("human_request"), "human_request"),
    ],
)
async def test_anything_else_after_cancellation_hands_off(h: Harness, understanding, reason):
    await h.reach("cancelled")
    result = await h.say("...", understanding)
    assert (result.template_key, result.handoff_reason) == ("handoff", reason)
    assert h.conv.status == "handoff" and "engine-handoff" in h.gateway.tags_added()


# --- a named day or time is never ignored ------------------------------------------

SUNDAY = dt.date(2026, 10, 11)


@pytest.mark.parametrize("window", [None, "after 17:00"])
async def test_i_work_till_5_anything_on_sunday_is_handled_as_a_proposal(h: Harness, window):
    """The live miss: the model said none_work (0.95) but did extract Sunday."""
    await h.reach("offered")
    offered_before = set(h.conv.current_offer)
    result = await h.say(
        "I work till 5, anything on sunday?",
        U("none_work", confidence=0.95, preferred_date=SUNDAY, preferred_time_window=window),
    )
    assert result.understanding["intent"] == "none_work"          # what the model said, logged as-is
    assert result.intent == "propose_time"                        # what the engine did with it
    # No inspections on Sunday, so: "that time's taken" + the openings closest to it.
    assert result.template_key == "propose_time_no_match" and result.handoff_reason is None
    conv = h.conv
    assert len(conv.current_offer) == 2 and not offered_before & set(conv.current_offer)
    days = {dt.datetime.fromisoformat(k).astimezone(h.client.tz).date() for k in conv.current_offer}
    assert all(abs((day - SUNDAY).days) <= 1 for day in days)     # Saturday or Monday, not "next up"
    assert conv.none_work_streak == 0                             # not counted as a bare rejection


async def test_neither_plus_a_day_offers_that_day_instead_of_just_more_slots(h: Harness):
    await h.reach("offered")
    result = await h.say("neither, how about friday",
                         U("none_work", preferred_date=dt.date(2026, 10, 9)))
    assert result.template_key == "propose_time_match" and result.reply.startswith("Fri 10/9 at ")


async def test_yes_plus_a_time_window_is_a_proposal_not_a_generic_offer(h: Harness):
    result = await h.say("yes but i work till 5",
                         U("affirm", preferred_time_window="after 17:00"))
    assert result.intent == "propose_time"
    assert result.template_key in ("propose_time_match", "propose_time_no_match")
    # Nothing is open after 5 PM, so the closest openings are the latest ones in the day.
    hours = {dt.datetime.fromisoformat(k).astimezone(h.client.tz).hour for k in h.conv.current_offer}
    assert hours == {15, 16}


async def test_named_day_during_a_reschedule_is_still_a_reschedule(h: Harness):
    await h.reach("booked")
    await h.say("can we move it", U("reschedule"))
    proposed = await h.say("neither, anything thursday?",
                           U("none_work", preferred_date=dt.date(2026, 10, 8)))
    assert proposed.intent == "propose_time" and proposed.template_key == "propose_time_match"
    moved = await h.say("yes", U("affirm"))
    assert moved.booking["action"] == "rescheduled" and len(h.calendar.appointments) == 1
    assert next(iter(h.calendar.appointments.values())).slot.date() == dt.date(2026, 10, 8)


async def test_low_confidence_still_wins_over_a_named_day(h: Harness):
    result = await h.say("mumble sunday?", U("none_work", confidence=0.4, preferred_date=SUNDAY))
    assert (result.intent, result.template_key) == ("unclear", "clarify")
