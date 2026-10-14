"""A new GHL opener to a contact the engine already knows (the `engine-new-opener` tag).

GHL's workflow adds the tag, then sends the opener. On the homeowner's reply the
engine starts over or carries on according to the conversation's state, and
takes the tag off. Opted out stays opted out.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from booking_engine.conversation import HANDOFF_QUIET, OPENER_KEPT, apply_new_opener
from booking_engine.models import Conversation
from tests.conftest import TZ, U
from tests.ghl_fake import slot
from tests.test_webhook import Site

NEW_OPENING = (
    "Hey, it's Roof Demo. Sorry we missed your call! Is this about your roof? "
    "I can get an inspector out this week if you'd like. (Reply STOP to opt out)"
)
STILL_ON = "Hey, you're still on for today at 11 AM. Want to keep that or move it?"


@pytest.fixture
def site() -> Site:
    return Site()


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def opener(site: Site, at: datetime) -> None:
    """The GHL workflow: Add Tag first, then Send SMS. Its clock moves to the opener."""
    site.ghl.tags |= {"engine-active", "engine-new-opener"}
    site.ghl.add_outbound(f"open-{at:%m%d%H%M%S}", NEW_OPENING, iso(at))
    site.now = at


async def reply(site: Site, body: str, understanding=None, *, after: float = 5.0):
    """The homeowner answers the latest opener `after` seconds later."""
    site.now += timedelta(seconds=after)
    return await site.text(body, understanding, at=iso(site.now))


async def book(site: Site) -> None:
    await site.text("yes", U("affirm"))
    await site.text("1", U("pick_slot", slot_choice=1))         # Monday 10/5 at 11 AM
    assert site.conv.status == "booked" and site.conv.appointment_id == "appt-1"


# --- a reply that arrives seconds after the opener ------------------------------

async def test_a_reply_seconds_after_the_opener_starts_a_finished_conversation_over(site: Site):
    await site.text("not interested", U("not_interested"))
    assert site.conv.status == "closed"

    opener(site, datetime(2026, 10, 6, 9, 0, tzinfo=TZ))
    assert (await reply(site, "Yes please", U("affirm"), after=2)).json() == {"status": "accepted"}

    assert site.ghl.sent[-1].startswith("Great, I've got")
    assert site.conv.status == "slots_offered"
    # The model saw the new opener as the message being answered.
    assert site.understander.contexts[-1].history[-1] == {"role": "engine", "text": NEW_OPENING}
    assert "engine-new-opener" not in site.ghl.tags
    assert site.ghl.bodies("PUT", "/opportunities/opp-1")[-1]["pipelineStageId"] == "st-replied"


async def test_the_next_text_carries_on_and_is_never_a_second_fresh_start(site: Site):
    await site.text("not interested", U("not_interested"))
    opener(site, datetime(2026, 10, 6, 9, 0, tzinfo=TZ))
    # GHL fails to take the tag off: the engine must still apply the opener only once.
    site.ghl.fail[("DELETE", "/contacts/c1/tags")].append(httpx.Response(400, json={}))
    await reply(site, "Yes please", U("affirm"))
    assert "engine-new-opener" in site.ghl.tags

    await reply(site, "1", U("pick_slot", slot_choice=1), after=20)
    assert site.conv.status == "booked"
    assert site.ghl.sent[-1].startswith("You're all set for")


async def test_a_brand_new_contact_with_the_tag_is_a_normal_first_reply(site: Site):
    opener(site, datetime(2026, 10, 5, 8, 59, tzinfo=TZ))
    await reply(site, "yes", U("affirm"))
    assert site.ghl.sent[-1].startswith("Great, I've got")
    assert site.understander.contexts[0].history == [{"role": "engine", "text": NEW_OPENING}]
    assert "engine-new-opener" not in site.ghl.tags


async def test_mid_conversation_a_yes_to_the_opener_never_books_a_stale_offer(site: Site):
    await site.text("thursday morning?", U("propose_time", preferred_date=slot(6, 10).date()))
    assert site.conv.offer_template == "propose_time_match"   # "want me to put you down?"

    opener(site, datetime(2026, 10, 5, 9, 30, tzinfo=TZ))
    await reply(site, "yes", U("affirm"))
    assert site.ghl.appointments == {}
    assert site.ghl.sent[-1].startswith("Great, I've got")


# --- opted out stays opted out --------------------------------------------------

async def test_an_opted_out_contact_given_the_tag_stays_opted_out(site: Site):
    await site.text("STOP")
    before = site.conv
    assert before.status == "opted_out"

    opener(site, datetime(2026, 10, 7, 9, 0, tzinfo=TZ))
    await reply(site, "yes", U("affirm"))
    await reply(site, "hello?", U("affirm"))

    after = site.conv
    assert site.ghl.sent == []
    assert (after.status, after.offered_slots, after.current_offer) == (
        "opted_out", before.offered_slots, before.current_offer)
    assert "engine-opted-out" in site.ghl.tags and "engine-new-opener" not in site.ghl.tags


# --- handoff: reopens only after 24 quiet hours, never if hostile ----------------

HANDED_OFF_AT = datetime(2026, 10, 5, 9, 1, tzinfo=TZ)      # the fake's first inbound time


async def hand_off(site: Site, intent: str = "human_request") -> None:
    await site.text("can someone call me", U(intent))
    assert site.conv.status == "handoff" and "engine-active" not in site.ghl.tags


async def test_a_handoff_under_24_hours_old_stays_with_the_person(site: Site):
    await hand_off(site)
    opener(site, HANDED_OFF_AT + HANDOFF_QUIET - timedelta(hours=1))
    sent = len(site.ghl.sent)

    response = await reply(site, "yes", U("affirm"))
    assert response.json() == {"status": "ignored", "reason": "handoff_not_reopened"}
    assert len(site.ghl.sent) == sent and site.conv.status == "handoff"
    # The opener workflow put engine-active back; it comes off again, the alert tag stays.
    assert "engine-handoff" in site.ghl.tags
    assert not {"engine-active", "engine-new-opener"} & site.ghl.tags

    response = await reply(site, "hello?", U("affirm"), after=60)
    assert response.json()["reason"] == "not_engine_active"


async def test_a_text_from_a_person_in_the_ghl_inbox_restarts_the_24_hours(site: Site):
    await hand_off(site)
    person = HANDED_OFF_AT + timedelta(hours=20)
    site.ghl.add_outbound("inbox-1", "Hi Jane, this is Mike from Roof Demo.", iso(person))
    opener(site, person + timedelta(hours=10))                # 34h after the handoff

    assert (await reply(site, "yes", U("affirm"))).json()["reason"] == "handoff_not_reopened"


async def test_a_missed_call_does_not_count_as_texting(site: Site):
    await hand_off(site)
    called = HANDED_OFF_AT + HANDOFF_QUIET + timedelta(hours=2)
    site.ghl.add_outbound("call-1", "", iso(called), kind="TYPE_CALL")
    opener(site, called + timedelta(seconds=30))

    assert (await reply(site, "yes", U("affirm"))).json() == {"status": "accepted"}
    assert site.conv.status == "slots_offered"


async def test_a_handoff_over_24_hours_old_starts_over(site: Site):
    await hand_off(site)
    opener(site, HANDED_OFF_AT + HANDOFF_QUIET + timedelta(hours=1))

    assert (await reply(site, "yes", U("affirm"))).json() == {"status": "accepted"}
    conv = site.conv
    assert (conv.status, conv.handoff_reason) == ("slots_offered", None)
    assert site.ghl.sent[-1].startswith("Great, I've got")
    assert not {"engine-handoff", "engine-new-opener"} & site.ghl.tags
    assert "engine-active" in site.ghl.tags


async def test_a_hostile_handoff_never_reopens(site: Site):
    await hand_off(site, "hostile")
    opener(site, HANDED_OFF_AT + timedelta(days=30))

    assert (await reply(site, "yes", U("affirm"))).json()["reason"] == "handoff_not_reopened"
    assert site.conv.status == "handoff" and "engine-active" not in site.ghl.tags


async def test_stop_after_an_opener_to_a_handed_off_contact_is_still_an_opt_out(site: Site):
    await hand_off(site)
    opener(site, HANDED_OFF_AT + timedelta(hours=2))
    assert (await reply(site, "STOP")).json() == {"status": "accepted"}
    assert site.conv.status == "opted_out" and "engine-opted-out" in site.ghl.tags


# --- booked: appointment still ahead vs. already past ----------------------------

async def test_booked_ahead_a_yes_to_the_opener_gets_a_reminder_not_silence(site: Site):
    await book(site)
    opener(site, datetime(2026, 10, 5, 9, 30, tzinfo=TZ))     # before the 11 AM inspection

    await reply(site, "Yes", U("affirm", acknowledgment_only=True))
    assert site.ghl.sent[-1] == STILL_ON
    assert site.conv.status == "booked" and len(site.ghl.appointments) == 1

    await reply(site, "keep it", U("affirm"), after=30)
    assert site.ghl.sent[-1] == "Perfect, see you then."

    sent = len(site.ghl.sent)
    await reply(site, "thanks!", U("affirm", acknowledgment_only=True), after=30)
    assert len(site.ghl.sent) == sent                         # the usual "ok" after booking

    await reply(site, "actually can we move it", U("reschedule"), after=30)
    assert site.ghl.sent[-1].startswith("No problem, I can move you to")


async def test_move_it_after_the_reminder_reschedules_instead_of_perfect(site: Site):
    await book(site)
    opener(site, datetime(2026, 10, 5, 9, 30, tzinfo=TZ))
    await reply(site, "Yes", U("affirm"))
    await reply(site, "can we do later in the week", U("reschedule"), after=30)
    assert site.ghl.sent[-1].startswith("No problem, I can move you to")
    assert "Perfect, see you then." not in site.ghl.sent


async def test_booked_ahead_moving_it_after_the_reminder_moves_the_same_appointment(site: Site):
    await book(site)
    opener(site, datetime(2026, 10, 5, 9, 30, tzinfo=TZ))
    await reply(site, "Yes", U("affirm"))
    await reply(site, "move it please", U("reschedule"), after=30)
    await reply(site, "2", U("pick_slot", slot_choice=2), after=30)
    assert list(site.ghl.appointments) == ["appt-1"]
    assert site.ghl.appointments["appt-1"]["startTime"] != "2026-10-05T11:00:00-05:00"


async def test_booked_in_the_past_starts_over_with_a_new_booking(site: Site):
    await book(site)
    site.ghl.slots = [slot(9, 10), slot(9, 14), slot(12, 9), slot(13, 15)]
    opener(site, datetime(2026, 10, 8, 9, 0, tzinfo=TZ))      # Monday's inspection is past

    await reply(site, "Yes", U("affirm"))
    conv = site.conv
    assert site.ghl.sent[-1].startswith("Great, I've got")
    assert (conv.status, conv.appointment_id, conv.booked_time) == ("slots_offered", None, None)

    await reply(site, "1", U("pick_slot", slot_choice=1), after=30)
    assert list(site.ghl.appointments) == ["appt-1", "appt-2"]  # a new one, the old is left
    assert site.ghl.appointments["appt-1"]["startTime"] == "2026-10-05T11:00:00-05:00"


# --- the rules on their own -----------------------------------------------------

def test_without_a_known_quiet_period_a_handoff_stays_handed_off():
    conv = Conversation(status="handoff", handoff_reason="human_request", current_offer=[])
    now = datetime(2026, 10, 9, 9, 0, tzinfo=TZ)
    assert apply_new_opener(conv, now=now, quiet_for=None) == "stays_handed_off"
    assert apply_new_opener(conv, now=now, quiet_for=timedelta.max) == "fresh"


def test_a_reopened_handoff_keeps_an_appointment_that_is_still_ahead():
    ahead = datetime(2026, 10, 12, 9, 0, tzinfo=TZ).astimezone(UTC).isoformat()
    conv = Conversation(status="handoff", handoff_reason="unclear_twice", appointment_id="appt-9",
                        booked_time=ahead, current_offer=[], offered_slots=[])
    now = datetime(2026, 10, 9, 9, 0, tzinfo=TZ)
    assert apply_new_opener(conv, now=now, quiet_for=timedelta(days=2)) == "fresh"
    assert (conv.status, conv.appointment_id) == ("booked", "appt-9")
    assert apply_new_opener(conv, now=now, quiet_for=None) == OPENER_KEPT
