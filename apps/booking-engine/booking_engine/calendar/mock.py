"""Mock calendar with realistic free slots. Used by the simulator and the tests.

Shape of a believable roofer's week: inspections start on the hour, Mon-Fri
8 AM-4 PM and Saturday mornings, never Sunday, with a deterministic ~40% of
the slots already taken by other jobs.
"""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from booking_engine.calendar.base import Calendar, CalendarError, SlotUnavailable

_WEEKDAY_HOURS = range(8, 17)   # last inspection starts 4 PM
_SATURDAY_HOURS = range(8, 12)


@dataclass
class MockAppointment:
    id: str
    slot: datetime
    contact_id: str
    title: str
    duration_minutes: int
    cancelled: bool = False


class MockCalendar(Calendar):
    def __init__(self, tz: ZoneInfo, *, seed: str = "stormbook", busy_ratio: float = 0.4) -> None:
        self._tz = tz
        self._seed = seed
        self._busy_ratio = busy_ratio
        self._taken: set[datetime] = set()
        self._ids = itertools.count(1)
        self.appointments: dict[str, MockAppointment] = {}
        self.fail_next: CalendarError | None = None   # tests: make the next call blow up

    def _busy_elsewhere(self, slot: datetime) -> bool:
        digest = hashlib.sha256(f"{self._seed}|{slot.isoformat()}".encode()).digest()
        return digest[0] / 255 < self._busy_ratio

    def _is_open(self, slot: datetime) -> bool:
        local = slot.astimezone(self._tz)
        if local.minute or local.second or local.microsecond:
            return False
        hours = {5: _SATURDAY_HOURS, 6: range(0)}.get(local.weekday(), _WEEKDAY_HOURS)
        return local.hour in hours and local not in self._taken and not self._busy_elsewhere(local)

    def _maybe_fail(self) -> None:
        if self.fail_next is not None:
            error, self.fail_next = self.fail_next, None
            raise error

    def take(self, slot: datetime) -> None:
        """Simulate someone else grabbing a slot (another lead, the office)."""
        self._taken.add(slot.astimezone(self._tz))

    async def free_slots(self, start: datetime, end: datetime) -> list[datetime]:
        self._maybe_fail()
        start, end = start.astimezone(self._tz), end.astimezone(self._tz)
        slots: list[datetime] = []
        day = start.date()
        while day <= end.date():
            for hour in _WEEKDAY_HOURS:
                slot = datetime.combine(day, time(hour), tzinfo=self._tz)
                if start <= slot <= end and self._is_open(slot):
                    slots.append(slot)
            day += timedelta(days=1)
        return slots

    async def book(
        self, slot: datetime, *, contact_id: str, title: str, duration_minutes: int
    ) -> str:
        self._maybe_fail()
        if not self._is_open(slot):
            raise SlotUnavailable(f"{slot.isoformat()} is no longer free")
        appointment = MockAppointment(
            id=f"mock-appt-{next(self._ids)}",
            slot=slot.astimezone(self._tz),
            contact_id=contact_id,
            title=title,
            duration_minutes=duration_minutes,
        )
        self._taken.add(appointment.slot)
        self.appointments[appointment.id] = appointment
        return appointment.id

    async def reschedule(
        self, appointment_id: str, slot: datetime, *, duration_minutes: int
    ) -> None:
        self._maybe_fail()
        appointment = self._get(appointment_id)
        if not self._is_open(slot):
            raise SlotUnavailable(f"{slot.isoformat()} is no longer free")
        self._taken.discard(appointment.slot)
        appointment.slot = slot.astimezone(self._tz)
        appointment.duration_minutes = duration_minutes
        self._taken.add(appointment.slot)

    async def cancel(self, appointment_id: str) -> None:
        self._maybe_fail()
        appointment = self._get(appointment_id)
        appointment.cancelled = True
        self._taken.discard(appointment.slot)

    def _get(self, appointment_id: str) -> MockAppointment:
        appointment = self.appointments.get(appointment_id)
        if appointment is None or appointment.cancelled:
            raise CalendarError(f"no active appointment {appointment_id!r}")
        return appointment
