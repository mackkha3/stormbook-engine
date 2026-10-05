"""Calendar and booking rules (spec section 6)."""

from __future__ import annotations

import datetime as dt
from datetime import datetime, timedelta

from booking_engine.calendar.base import CalendarError
from booking_engine.slots import eligible, format_slot, pick_spread, slot_key
from tests.conftest import NOW, SLOT_1, SLOT_2, TZ, Harness, U


def at(day: int, hour: int) -> datetime:
    return datetime(2026, 10, day, hour, 0, tzinfo=TZ)


def offer(h: Harness) -> list[datetime]:
    return [datetime.fromisoformat(k).astimezone(TZ) for k in h.conv.current_offer]


def test_slot_format():
    assert format_slot(datetime(2026, 10, 7, 10, 0, tzinfo=TZ), TZ) == "Wed 10/7 at 10:00 AM"
    assert format_slot(datetime(2026, 10, 6, 15, 0, tzinfo=dt.UTC), TZ) == "Tue 10/6 at 10:00 AM"
    assert format_slot(at(12, 12), TZ) == "Mon 10/12 at 12:00 PM"


def test_eligible_respects_lead_time_window_and_exclusions():
    free = [at(5, 10), at(5, 11), at(6, 9), at(12, 9), at(12, 10)]
    kept = eligible(free, now=NOW, tz=TZ, min_lead_minutes=120, window_days=7,
                    exclude={slot_key(at(6, 9))})
    assert kept == [at(5, 11), at(12, 9)]      # 10 AM is inside lead time; 10/12 10 AM is past 7 days


def test_spread_prefers_a_different_day_and_part_of_day():
    back_to_back = [at(5, 11), at(5, 12), at(5, 13), at(6, 9), at(6, 14)]
    assert pick_spread(back_to_back, 2) == [at(5, 11), at(6, 14)]
    assert pick_spread([at(5, 9), at(5, 10), at(5, 15)], 2) == [at(5, 9), at(5, 15)]
    assert pick_spread([at(5, 9), at(5, 10)], 2) == [at(5, 9), at(5, 10)]     # last resort
    assert pick_spread([at(5, 9)], 2) == [at(5, 9)]


async def test_only_calendar_slots_are_offered_inside_lead_time_and_window(h: Harness):
    result = await h.say("yes", U("affirm"))
    slots = offer(h)
    assert slots == [SLOT_1, SLOT_2]
    assert result.reply.startswith("Great! I can get an inspector out Mon 10/5 at 11:00 AM or "
                                   "Tue 10/6 at 12:00 PM.")
    free = await h.calendar.free_slots(NOW, NOW + timedelta(days=7))
    assert all(s in free and NOW + timedelta(minutes=120) <= s <= NOW + timedelta(days=7)
               for s in slots)


async def test_taken_slots_are_never_offered(h: Harness):
    h.calendar.take(SLOT_1)
    h.calendar.take(SLOT_2)
    await h.say("yes", U("affirm"))
    assert SLOT_1 not in offer(h) and SLOT_2 not in offer(h)


async def test_none_work_offers_different_times_never_a_repeat(h: Harness):
    await h.reach("offered")
    result = await h.say("neither", U("none_work"))
    second = offer(h)
    assert result.template_key == "offer_more_slots"
    assert not {SLOT_1, SLOT_2} & set(second)
    assert all(s.date() not in (SLOT_1.date(), SLOT_2.date()) for s in second)
    assert len(h.conv.offered_slots) == 4


async def test_the_same_time_is_never_offered_twice_in_a_conversation(h: Harness):
    seen: list[str] = []
    await h.say("yes", U("affirm"))
    for i in range(6):
        seen += h.conv.current_offer
        await h.say("neither", U("none_work"))                       # → more slots
        seen += h.conv.current_offer
        await h.say("no", U("none_work"))                            # → ask_preference
        await h.say("?", U("propose_time", preferred_date=dt.date(2026, 10, 11)))  # Sunday: no match
        if h.conv.status == "handoff":
            break
    assert len(seen) == len(set(seen))


async def test_propose_time_offers_matches_inside_the_requested_window(h: Harness):
    result = await h.say("tmrw aft 3??", U(
        "propose_time", preferred_date=dt.date(2026, 10, 6), preferred_time_window="after 15:00"))
    assert result.template_key == "propose_time_match"
    assert offer(h) == [at(6, 15), at(6, 16)]
    assert result.reply.startswith("Tue 10/6 at 3:00 PM is open.")


async def test_propose_time_with_nothing_open_offers_the_closest_openings(h: Harness):
    for hour in range(8, 12):
        h.calendar.take(at(10, hour))                 # Saturday morning fully booked
    result = await h.say("sat morning?", U(
        "propose_time", preferred_date=dt.date(2026, 10, 10), preferred_time_window="morning"))
    assert result.template_key == "propose_time_no_match"
    assert offer(h) == [at(9, 15), at(9, 16)]         # Friday afternoon is the nearest opening


async def test_booking_creates_a_60_minute_appointment_and_updates_the_crm(h: Harness):
    await h.reach("offered")
    result = await h.say("2", U("pick_slot", slot_choice=2))
    (appointment,) = h.calendar.appointments.values()
    assert (appointment.slot, appointment.duration_minutes) == (SLOT_2, 60)
    assert appointment.title == "Roof inspection: Jane Doe"
    assert appointment.contact_id == "contact-1"
    assert result.reply == (
        "You're all set for Tue 10/6 at 12:00 PM. Your Roof Demo inspector will meet you at "
        "the property. Reply here if anything changes."
    )
    conv = h.conv
    assert (conv.status, conv.appointment_id, conv.booked_time) == (
        "booked", appointment.id, slot_key(SLOT_2))
    calls = [c for c in h.gateway.calls if c["call"] != "send_sms"]
    assert calls == [
        {"call": "set_stage", "contact_id": "contact-1", "stage": "booked"},
        {"call": "add_tags", "contact_id": "contact-1", "tags": ["booked"]},
    ]


async def test_appointment_title_falls_back_to_the_phone_number():
    h = Harness()
    h.contact = h.contact.__class__(contact_id="contact-2", phone="+19035550123")
    await h.reach("booked")
    assert next(iter(h.calendar.appointments.values())).title == "Roof inspection: +19035550123"


async def test_slot_is_rechecked_immediately_before_booking(h: Harness):
    await h.reach("offered")
    h.calendar.take(SLOT_1)                           # someone else grabs it after it was offered
    result = await h.say("1", U("pick_slot", slot_choice=1))
    assert result.template_key == "propose_time_no_match"
    assert result.booking == {"action": "slot_taken", "slot": SLOT_1.isoformat()}
    assert not h.calendar.appointments and h.conv.status == "slots_offered"
    fresh = offer(h)
    assert len(fresh) == 2 and not {SLOT_1, SLOT_2} & set(fresh)
    assert (await h.say("1", U("pick_slot", slot_choice=1))).template_key == "booked"


async def test_an_offered_slot_that_slipped_inside_lead_time_is_not_booked(h: Harness):
    await h.reach("offered")
    h.now = at(5, 10)                                 # SLOT_1 (11 AM) is now under 2 hours away
    result = await h.say("1", U("pick_slot", slot_choice=1))
    assert result.template_key == "propose_time_no_match" and not h.calendar.appointments


async def test_no_free_slots_sends_no_slots_available_and_hands_off():
    h = Harness()
    h.calendar._busy_ratio = 1.1                      # the roofer's calendar is completely full
    result = await h.say("yes", U("affirm"))
    assert (result.template_key, result.handoff_reason) == (
        "no_slots_available", "no_slots_available")
    assert h.conv.status == "handoff"


async def test_calendar_outage_hands_off(h: Harness):
    h.calendar.fail_next = CalendarError("GHL timed out")
    result = await h.say("yes", U("affirm"))
    assert result.template_key == "handoff" and result.handoff_reason.startswith("calendar_error")


async def test_reschedule_moves_the_existing_appointment(h: Harness):
    await h.reach("booked")
    offered = await h.say("can we move it", U("reschedule"))
    assert offered.template_key == "reschedule" and SLOT_1 not in offer(h)
    new_slot = offer(h)[0]
    result = await h.say("1", U("pick_slot", slot_choice=1))
    (appointment,) = h.calendar.appointments.values()
    assert result.booking["action"] == "rescheduled" and appointment.slot == new_slot
    assert format_slot(new_slot, TZ) in result.reply
    assert await h.calendar.is_free(SLOT_1)           # the old time opened back up


async def test_cancel_cancels_the_appointment_confirms_and_notifies(h: Harness):
    await h.reach("booked")
    result = await h.say("cancel", U("cancel"))
    (appointment,) = h.calendar.appointments.values()
    assert appointment.cancelled and result.template_key == "cancelled"
    assert h.conv.status == "closed"
    assert "engine-handoff" in h.gateway.tags_added()
    assert "Reason: homeowner_cancelled" in h.gateway.notes()[0]
