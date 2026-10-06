"""Value types for one turn: what came in, what was decided, what happened."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from booking_engine.calendar.base import Calendar
from booking_engine.config_loader import ClientConfig
from booking_engine.models import ACTIVE, SLOTS_OFFERED, Conversation
from booking_engine.slots import eligible, from_key, slot_key
from booking_engine.templates import Templates
from booking_engine.understanding import Understanding


@dataclass(frozen=True)
class Contact:
    contact_id: str
    phone: str
    name: str | None = None
    # Which GHL workflow sent the opening text this homeowner is replying to.
    lead_source: str = "missed_call"
    # Phase 2: the actual last outbound message from the GHL conversation. When set it
    # is used instead of the configured opening text for `lead_source`.
    opening_text: str | None = None


@dataclass(frozen=True)
class InboundMessage:
    message_id: str
    body: str
    timestamp: datetime | None = None
    photo: bool = False          # the text carried a picture (or other attachment)


@dataclass
class TurnResult:
    inbound_text: str
    status: str
    intent: str | None = None
    understanding: dict | None = None
    template_key: str | None = None
    reply: str | None = None
    booking: dict | None = None
    handoff_reason: str | None = None
    crm_calls: list[dict[str, Any]] = field(default_factory=list)
    ignored: bool = False
    photo: bool = False


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
    cancelled: bool = False              # appointment cancelled: tag + note for the roofer
    opted_out: bool = False              # tag the contact (and warn the roofer if still booked)
    reopened: bool = False               # a cancelled conversation came back to life
    booking: dict | None = None          # what happened on the calendar, for the turn log
    mark_booked: bool = False            # move opportunity to `booked`, add the booked tag


def handoff(reason: str, *, template: str | None = "handoff", urgent: bool = False) -> Decision:
    return Decision(template_key=template, handoff=Handoff(reason, urgent))


@dataclass
class Turn:
    conv: Conversation
    u: Understanding | None      # None for a picture with no words: the model is not asked
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
