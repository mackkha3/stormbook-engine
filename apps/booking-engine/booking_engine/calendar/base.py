"""Calendar interface. The engine only ever talks to this — mock in Phase 1, GHL in Phase 2."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime


class CalendarError(Exception):
    """The calendar backend failed (network, API error). The engine hands off."""


class SlotUnavailable(CalendarError):
    """The requested slot was taken between offering it and booking it."""


class Calendar(ABC):
    @abstractmethod
    async def free_slots(self, start: datetime, end: datetime) -> list[datetime]:
        """Open inspection start times in [start, end], timezone-aware."""

    @abstractmethod
    async def book(
        self, slot: datetime, *, contact_id: str, title: str, duration_minutes: int
    ) -> str:
        """Create the appointment and return its id. Raises SlotUnavailable if taken."""

    @abstractmethod
    async def reschedule(
        self, appointment_id: str, slot: datetime, *, duration_minutes: int
    ) -> None:
        """Move an existing appointment. Raises SlotUnavailable if `slot` is taken."""

    @abstractmethod
    async def cancel(self, appointment_id: str) -> None:
        """Cancel an existing appointment."""

    @abstractmethod
    async def retitle(self, appointment_id: str, title: str) -> None:
        """Rename an existing appointment (the address arrived after booking)."""

    async def is_free(self, slot: datetime) -> bool:
        """Fresh re-check that `slot` is still open. Called immediately before booking."""
        return any(s == slot for s in await self.free_slots(slot, slot))
