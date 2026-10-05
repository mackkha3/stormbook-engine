"""Intent → action: the conversation state machine.

`decide` takes one understood turn and returns a `Decision`: which template
to send (if any), which slots fill it, and whether to hand off. It updates the
conversation's state in place and performs calendar actions (book, move,
cancel); the engine does the rendering, the safety check, and the CRM calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from booking_engine.calendar.base import Calendar, CalendarError, SlotUnavailable
from booking_engine.config_loader import ClientConfig
from booking_engine.models import ACTIVE, BOOKED, CLOSED, OPTED_OUT, SLOTS_OFFERED, Conversation
from booking_engine.slots import (
    closest,
    eligible,
    from_key,
    matching,
    pick_spread,
    slot_key,
    target_time,
)
from booking_engine.templates import Templates
from booking_engine.understanding import Understanding

REQUIRED_TEMPLATES = {
    "offer_slots", "offer_more_slots", "ask_preference", "propose_time_match",
    "propose_time_no_match", "booked", "deflect", "basic_who", "basic_area", "is_bot",
    "clarify", "reschedule", "cancelled", "handoff", "emergency", "not_interested",
    "no_slots_available",
}
_AREA_QUESTION = re.compile(
    r"\b(area|areas|cover|service|serve|located|location|where|far|zip|county|city|town|come out to)\b",
    re.IGNORECASE,
)


@dataclass
class Handoff:
    reason: str
    urgent: bool = False


@dataclass
class Decision:
    template_key: str | None = None
    slots: list[datetime] = field(default_factory=list)     # fill {slot_1}, {slot_2}, ...
    booked_time: datetime | None = None                     # fills {booked_time}
    verified: list[datetime] = field(default_factory=list)  # times the calendar returned this turn
    handoff: Handoff | None = None
    notify: str | None = None            # tell the roofer without a handoff message (cancel)
    booking: dict | None = None          # what happened on the calendar, for the turn log
    mark_booked: bool = False            # move opportunity to `booked`, add the booked tag


def handoff(reason: str, *, template: str | None = "handoff", urgent: bool = False) -> Decision:
    return Decision(template_key=template, handoff=Handoff(reason, urgent))


@dataclass
class Turn:
    conv: Conversation
    u: Understanding
    text: str
    client: ClientConfig
    calendar: Calendar
    templates: Templates
    now: datetime
    prev_unclear: int = 0
    prev_none_work: int = 0

    @property
    def current(self) -> list[datetime]:
        return [from_key(k, self.client.tz) for k in self.conv.current_offer]

    @property
    def has_preference(self) -> bool:
        return self.u.preferred_date is not None or self.u.preferred_time_window is not None

    async def free(self, *, unoffered_only: bool = False) -> list[datetime]:
        """Fresh free slots from the calendar, inside lead time and search window."""
        c = self.client
        raw = await self.calendar.free_slots(
            self.now + timedelta(minutes=c.min_lead_time_minutes),
            self.now + timedelta(days=c.search_window_days),
        )
        return eligible(
            raw, now=self.now, tz=c.tz, min_lead_minutes=c.min_lead_time_minutes,
            window_days=c.search_window_days,
            exclude=set(self.conv.offered_slots) if unoffered_only else None,
        )

    def set_offer(self, key: str, slots: list[datetime], verified: list[datetime]) -> Decision:
        conv = self.conv
        conv.current_offer = [slot_key(s) for s in slots]
        conv.offered_slots = sorted(set(conv.offered_slots) | set(conv.current_offer))
        conv.offer_template = key
        if conv.status == ACTIVE:
            conv.status = SLOTS_OFFERED
        return Decision(template_key=key, slots=slots, verified=verified)


async def decide(t: Turn) -> Decision:
    try:
        return await _decide(t)
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
        conv.status = OPTED_OUT
        conv.current_offer = []
        return Decision()
    if intent == "hostile":
        return handoff("hostile", template=None)
    if intent == "emergency":
        return handoff("emergency", template="emergency", urgent=True)
    if intent == "human_request":
        return handoff("human_request")
    if intent == "cancel" and booked:
        return await _cancel(t)
    if intent in ("cancel", "not_interested"):
        if booked:
            return handoff("not_interested_after_booking")
        conv.status = CLOSED
        conv.current_offer = []
        return Decision(template_key="not_interested")

    if booked and not conv.current_offer:
        # Booked and nothing pending: only a move request keeps the engine talking.
        if intent == "affirm":
            return Decision()    # "ok thanks" after the confirmation needs no reply
        if intent == "reschedule" or (intent in ("propose_time", "pick_slot") and t.has_preference):
            return await (_propose(t) if t.has_preference else _offer(t, "reschedule", fresh=True))
        return handoff(f"{intent}_after_booking")

    if intent == "affirm":
        if not conv.current_offer:
            return await _offer(t, "offer_slots")
        if conv.offer_template == "propose_time_match":
            return await _book(t, t.current[0])      # "yes" to "want me to lock that in?"
        return await _unclear(t)                     # "yes" to "1 or 2?" — ask which
    if intent == "pick_slot":
        if u.slot_choice and u.slot_choice <= len(conv.current_offer):
            return await _book(t, t.current[u.slot_choice - 1])
        return await (_propose(t) if t.has_preference else _unclear(t))
    if intent == "propose_time":
        return await (_propose(t) if t.has_preference else _unclear(t))
    if intent == "reschedule" and t.has_preference:
        return await _propose(t)
    if intent == "reschedule" and booked:
        return await _offer(t, "reschedule", fresh=True)
    if intent in ("none_work", "reschedule"):
        conv.none_work_streak = t.prev_none_work + 1
        if conv.none_work_streak >= 2:
            conv.current_offer = []
            conv.awaiting_preference = True
            return Decision(template_key="ask_preference")
        return await _offer(t, "offer_more_slots", fresh=True)
    if intent == "deferrable_question":
        return await _offer(t, "deflect")
    if intent == "basic_question":
        return await _offer(t, "basic_area" if _AREA_QUESTION.search(t.text) else "basic_who")
    if intent == "is_bot":
        return await _offer(t, "is_bot")
    return await _unclear(t)


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
    moving = conv.appointment_id is not None and conv.status == BOOKED
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
    await t.calendar.cancel(conv.appointment_id)
    conv.status = CLOSED
    conv.current_offer = []
    return Decision(
        template_key="cancelled",
        notify="homeowner_cancelled",
        booking={"action": "cancelled", "appointment_id": conv.appointment_id},
    )
