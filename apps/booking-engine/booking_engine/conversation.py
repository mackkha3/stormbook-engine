"""Intent → action: the conversation state machine.

`decide` takes one understood turn and returns a `Decision`: which template
to send (if any), which slots fill it, and whether to hand off. It updates the
conversation's state in place and performs calendar actions (book, move,
cancel); the engine does the rendering, the safety check, and the CRM calls.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from booking_engine.calendar.base import CalendarError, SlotUnavailable
from booking_engine.models import ACTIVE, BOOKED, CANCELLED, CLOSED, OPTED_OUT
from booking_engine.slots import (
    closest,
    from_key,
    matching,
    pick_spread,
    slot_key,
    target_time,
)
from booking_engine.turn import Decision, Turn, handoff

REQUIRED_TEMPLATES = {
    "offer_slots", "offer_more_slots", "ask_preference", "propose_time_match",
    "propose_time_no_match", "booked", "deflect", "deflect_again", "basic_who", "basic_area", "is_bot",
    "which_one", "clarify", "reschedule", "cancelled", "handoff", "emergency", "not_interested",
    "no_slots_available", "photo_received", "photo_received_booked",
}
# Opening texts GHL sends, one per lead source (templates.yaml: opening_<lead_source>).
# Never sent by the engine; used to seed a new conversation's history.
LEAD_SOURCES = ("missed_call", "web_form")
DEFAULT_LEAD_SOURCE = "missed_call"
REQUIRED_TEMPLATES |= {f"opening_{source}" for source in LEAD_SOURCES}

REOPEN_INTENTS = ("affirm", "propose_time", "pick_slot", "none_work")
RESCHEDULE_WHEN_BOOKED = ("reschedule", "propose_time", "pick_slot", "none_work", "affirm")


async def decide(t: Turn) -> Decision:
    was_cancelled = t.conv.status == CANCELLED
    try:
        decision = await _decide(t)
    except CalendarError as exc:
        decision = handoff(f"calendar_error: {exc}")
    decision.reopened = was_cancelled and t.conv.status != CANCELLED
    return decision


async def decide_photo(t: Turn) -> Decision:
    """A picture with no words. Code rule, no model: thank them and keep moving to a time."""
    if t.conv.status == BOOKED:
        return Decision(template_key="photo_received_booked")
    t.conv.awaiting_preference = False
    try:
        return await _offer(t, "photo_received")
    except CalendarError as exc:
        return handoff(f"calendar_error: {exc}")


async def _decide(t: Turn) -> Decision:
    conv, u = t.conv, t.u
    intent = u.intent
    booked = conv.status == BOOKED
    t.prev_unclear, t.prev_none_work = conv.unclear_streak, conv.none_work_streak
    conv.unclear_streak = conv.none_work_streak = 0

    # After ask_preference, whatever day/time they name is their proposal.
    waiting, conv.awaiting_preference = conv.awaiting_preference, False
    if waiting and t.has_preference and intent in (
        "affirm", "pick_slot", "none_work", "reschedule", "unclear"
    ):
        intent = "propose_time"

    if intent == "opt_out":
        conv.status = OPTED_OUT      # a booked appointment is deliberately left in place
        conv.current_offer = []
        return Decision(opted_out=True)
    if intent == "hostile":
        return handoff("hostile", template=None)
    if intent == "emergency":
        return handoff("emergency", template="emergency", urgent=True)
    if intent == "human_request":
        return handoff("human_request")
    if conv.status == CANCELLED:
        # They cancelled earlier. A bare "ok"/"thanks" needs no reply; a clear wish to
        # schedule reopens the conversation; anything else goes to a human.
        if u.acknowledgment_only and intent in ("affirm", "unclear"):
            return Decision()
        if intent not in REOPEN_INTENTS:
            return handoff(f"{intent}_after_cancellation")
        conv.status = ACTIVE
        conv.engine_message_count = 0        # the message cap restarts with the new attempt
    if intent == "cancel" and booked:
        return await _cancel(t)
    if intent in ("cancel", "not_interested"):
        if booked:
            return handoff("not_interested_after_booking")
        conv.status = CLOSED
        conv.current_offer = []
        return Decision(template_key="not_interested")

    if booked:
        # There is an appointment, so anything about a different time is a reschedule,
        # whatever the model called it. The only bookings allowed from here are moves of
        # that appointment (see _book): answering an offer the engine made, by number
        # or with a plain "yes".
        if u.different_day and u.preferred_date is None:
            # "A different day": nothing on the day they are booked for now.
            t.avoid_date = from_key(conv.booked_time, t.client.tz).date()
        answers_offer = bool(conv.current_offer) and (
            (intent == "pick_slot" and u.slot_choice and u.slot_choice <= len(conv.current_offer))
            or (intent == "affirm" and not t.has_preference)
        )
        if not answers_offer:
            if intent in RESCHEDULE_WHEN_BOOKED and (intent != "affirm" or t.has_preference):
                if t.has_preference:
                    return await _propose(t)
                # Turning down offered reschedule times follows the first-booking rule:
                # a new pair once, then ask for their own day and time.
                if intent == "none_work" and conv.current_offer and _second_none_work(t):
                    return _ask_preference(t)
                return await _offer(t, "reschedule", fresh=True)
            if intent == "affirm":
                return Decision()    # "ok thanks" after the confirmation needs no reply
            if not conv.current_offer:
                return handoff(f"{intent}_after_booking")

    if intent == "affirm":
        if not conv.current_offer:
            return await _offer(t, "offer_slots")
        if conv.offer_template == "propose_time_match":
            return await _book(t, t.current[0])      # "yes" to "want me to lock that in?"
        return await _offer(t, "which_one")          # "yes" to "1 or 2?" — ask which
    if intent == "pick_slot":
        if u.slot_choice and u.slot_choice <= len(conv.current_offer):
            return await _book(t, t.current[u.slot_choice - 1])
        return await (_propose(t) if t.has_preference else _unclear(t))
    if intent == "propose_time":
        return await (_propose(t) if t.has_preference else _unclear(t))
    if intent == "reschedule" and t.has_preference:
        return await _propose(t)
    if intent in ("none_work", "reschedule"):
        if _second_none_work(t):
            return _ask_preference(t)
        return await _offer(t, "offer_more_slots", fresh=True)
    if intent == "deferrable_question":
        return await _deflect(t)
    if intent == "basic_question":
        # "who" or no topic → who we are; "other" has no approved answer → defer to the inspector.
        if u.question_topic == "other":
            return await _deflect(t)
        return await _offer(t, "basic_area" if u.question_topic == "area" else "basic_who")
    if intent == "is_bot":
        return await _offer(t, "is_bot")
    return await _unclear(t)


def _second_none_work(t: Turn) -> bool:
    """Count this rejection; True once it is the second in a row."""
    t.conv.none_work_streak = t.prev_none_work + 1
    return t.conv.none_work_streak >= 2


def _ask_preference(t: Turn) -> Decision:
    """Stop guessing: ask for their own day and time. The answer is treated as a proposal."""
    t.conv.current_offer = []
    t.conv.awaiting_preference = True
    return Decision(template_key="ask_preference")


async def _deflect(t: Turn) -> Decision:
    """Defer the question to the inspector, never with the same wording twice in a row."""
    again = t.conv.offer_template == "deflect"
    return await _offer(t, "deflect_again" if again else "deflect")


async def _unclear(t: Turn) -> Decision:
    t.conv.unclear_streak = t.prev_unclear + 1
    if t.conv.unclear_streak >= 2:
        return handoff("unclear_twice")
    return await _offer(t, "clarify")


def _no_slots(verified: list[datetime], booking: dict | None = None) -> Decision:
    decision = handoff("no_slots_available", template="no_slots_available")
    decision.verified, decision.booking = verified, booking
    return decision


async def _offer(t: Turn, key: str, *, fresh: bool = False) -> Decision:
    """Send `key` with slots: the ones already on the table, or new ones if `fresh`.

    New slots are never ones offered before, and are spread away from the
    current (just rejected) offer.
    """
    n = t.templates.slot_count(key)
    free = await t.free()
    keep = [] if fresh else [s for s in t.current if s in free][:n]
    if len(keep) < n:
        offered = set(t.conv.offered_slots)
        unoffered = [s for s in free if slot_key(s) not in offered]
        keep = sorted(keep + pick_spread(unoffered, n - len(keep), avoid=keep or t.current))
    if len(keep) < n:
        return _no_slots(free)
    return t.set_offer(key, keep, free)


async def _propose(t: Turn) -> Decision:
    """The homeowner named a day/time: offer what matches, or the closest openings."""
    u = t.u
    n = t.templates.slot_count("propose_time_match")
    free = await t.free()
    offered = set(t.conv.offered_slots)
    unoffered = [s for s in free if slot_key(s) not in offered]
    target = target_time(u.preferred_date, u.preferred_time_window, now=t.now, tz=t.client.tz)

    # Their own request may match a time offered earlier; that one is fair to show again.
    matches = matching(free, u.preferred_date, u.preferred_time_window)
    if matches:
        first = closest(matches, target, 1)[0]
        others = [s for s in matches if s != first]
        rest = pick_spread(others, n - 1, avoid=[first])
        if len(rest) < n - 1:
            pool = [s for s in unoffered if s != first and s not in rest]
            rest += closest(pool, first, n - 1 - len(rest))
        if len(rest) == n - 1:
            return t.set_offer("propose_time_match", [first, *rest], free)
    chosen = closest(unoffered, target, t.templates.slot_count("propose_time_no_match"))
    if len(chosen) < t.templates.slot_count("propose_time_no_match"):
        return _no_slots(free)
    return t.set_offer("propose_time_no_match", chosen, free)


async def _book(t: Turn, slot: datetime) -> Decision:
    """Re-check the slot is still free, then book it (or move the existing appointment)."""
    conv, c = t.conv, t.client
    # An existing appointment is always moved; a contact never gets a second one.
    moving = conv.appointment_id is not None
    still_free = slot >= t.now + timedelta(minutes=c.min_lead_time_minutes)
    try:
        still_free = still_free and await t.calendar.is_free(slot)
        if still_free and moving:
            await t.calendar.reschedule(
                conv.appointment_id, slot, duration_minutes=c.inspection_duration_minutes
            )
        elif still_free:
            conv.appointment_id = await t.calendar.book(
                slot,
                contact_id=conv.contact_id,
                title=f"Roof inspection: {conv.contact_name or conv.contact_phone}",
                duration_minutes=c.inspection_duration_minutes,
            )
    except SlotUnavailable:
        still_free = False

    if not still_free:
        n = t.templates.slot_count("propose_time_no_match")
        fresh = await t.free(unoffered_only=True)
        # The opening nearest the one they wanted, plus spread-out alternatives.
        chosen = closest(fresh, slot, 1)
        chosen = sorted(chosen + pick_spread([s for s in fresh if s not in chosen], n - 1, avoid=chosen))
        result = {"action": "slot_taken", "slot": slot.isoformat()}
        if len(chosen) < n:
            return _no_slots(fresh, result)
        decision = t.set_offer("propose_time_no_match", chosen, fresh)
        decision.booking = result
        return decision

    conv.status = BOOKED
    conv.booked_time = slot_key(slot)
    conv.current_offer = []
    conv.offer_template = None
    return Decision(
        template_key="booked",
        booked_time=slot,
        verified=[slot],
        mark_booked=True,
        booking={
            "action": "rescheduled" if moving else "booked",
            "appointment_id": conv.appointment_id,
            "slot": slot.isoformat(),
            "duration_minutes": c.inspection_duration_minutes,
        },
    )


async def _cancel(t: Turn) -> Decision:
    conv = t.conv
    appointment_id = conv.appointment_id
    await t.calendar.cancel(appointment_id)
    conv.status = CANCELLED
    conv.current_offer = []
    conv.appointment_id = conv.booked_time = None
    return Decision(
        template_key="cancelled",
        cancelled=True,
        booking={"action": "cancelled", "appointment_id": appointment_id},
    )
