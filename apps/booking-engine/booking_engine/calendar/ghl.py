"""The roofer's real GHL calendar.

Free slots come only from GHL's free-slots endpoint. Booking does not bypass
GHL's own slot validation, so a slot taken in the last second is refused by
GHL and surfaces as `SlotUnavailable`.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from booking_engine.calendar.base import Calendar, CalendarError, SlotUnavailable
from booking_engine.config_loader import ClientConfig
from booking_engine.ghl_client import GhlClient, GhlError

_MAX_RANGE = timedelta(days=30)     # GHL refuses free-slot ranges over 31 days


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


class GhlCalendar(Calendar):
    def __init__(self, ghl: GhlClient, client: ClientConfig) -> None:
        self._ghl = ghl
        self._calendar_id = client.calendar_id
        self._timezone = client.timezone
        self._tz = client.tz

    async def free_slots(self, start: datetime, end: datetime) -> list[datetime]:
        slots: set[datetime] = set()
        chunk_start = start
        try:
            while chunk_start <= end:
                chunk_end = min(chunk_start + _MAX_RANGE, end)
                raw = await self._ghl.free_slots(
                    self._calendar_id, start_ms=_ms(chunk_start), end_ms=_ms(chunk_end),
                    timezone=self._timezone,
                )
                slots.update(self._parse(s) for s in raw)
                if chunk_end >= end:
                    break
                chunk_start = chunk_end
        except (GhlError, ValueError) as exc:
            raise CalendarError(f"free slots lookup failed: {exc}") from exc
        return sorted(s for s in slots if start <= s <= end)

    def _parse(self, value: str) -> datetime:
        slot = datetime.fromisoformat(value)
        if slot.tzinfo is None:             # GHL sends an offset; be safe if it ever doesn't
            slot = slot.replace(tzinfo=self._tz)
        return slot.astimezone(self._tz)

    async def is_free(self, slot: datetime) -> bool:
        # Ask for the slot's whole local day: a zero-width range is not a documented query.
        local = slot.astimezone(self._tz)
        day_start = datetime.combine(local.date(), time.min, tzinfo=self._tz)
        return slot in await self.free_slots(day_start, day_start + timedelta(days=1))

    async def book(
        self, slot: datetime, *, contact_id: str, title: str, duration_minutes: int
    ) -> str:
        start = slot.astimezone(self._tz)
        try:
            return await self._ghl.create_appointment(
                calendar_id=self._calendar_id, contact_id=contact_id, title=title,
                start=start.isoformat(),
                end=(start + timedelta(minutes=duration_minutes)).isoformat(),
            )
        except GhlError as exc:
            raise self._translate(exc, slot) from exc

    async def reschedule(
        self, appointment_id: str, slot: datetime, *, duration_minutes: int
    ) -> None:
        start = slot.astimezone(self._tz)
        try:
            await self._ghl.update_appointment(
                appointment_id, calendarId=self._calendar_id, startTime=start.isoformat(),
                endTime=(start + timedelta(minutes=duration_minutes)).isoformat(),
            )
        except GhlError as exc:
            raise self._translate(exc, slot) from exc

    async def cancel(self, appointment_id: str) -> None:
        try:
            await self._ghl.update_appointment(appointment_id, appointmentStatus="cancelled")
        except GhlError as exc:
            raise CalendarError(f"cancel failed: {exc}") from exc

    async def retitle(self, appointment_id: str, title: str) -> None:
        try:
            await self._ghl.update_appointment(appointment_id, title=title)
        except GhlError as exc:
            raise CalendarError(f"retitle failed: {exc}") from exc

    @staticmethod
    def _translate(exc: GhlError, slot: datetime) -> CalendarError:
        """GHL rejects a taken slot with a 4xx whose message mentions the slot."""
        if exc.status in (400, 409, 422) and "slot" in exc.body.lower():
            return SlotUnavailable(f"{slot.isoformat()} is no longer free")
        return CalendarError(f"booking failed: {exc}")
