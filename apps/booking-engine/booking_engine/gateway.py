"""Everything the engine does to the CRM: send the SMS, tags, notes, pipeline stage.

Phase 1 ships only the in-memory `RecordingGateway` (simulator + tests).
Phase 2's ghl_client implements the same interface against the GHL API.
`send_sms` takes a RenderedMessage, never a bare string, so only template
output can be sent.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from booking_engine.templates import RenderedMessage

TAG_ACTIVE = "engine-active"
TAG_HANDOFF = "engine-handoff"
TAG_EMERGENCY = "engine-emergency"
TAG_BOOKED = "booked"
TAG_OPTED_OUT = "engine-opted-out"
TAG_OPTOUT_BOOKED = "engine-optout-booked"   # opted out but still has an inspection booked
TAG_CANCELLED = "engine-cancelled"


class CrmGateway(ABC):
    @abstractmethod
    async def send_sms(self, contact_id: str, message: RenderedMessage) -> None: ...

    @abstractmethod
    async def add_tags(self, contact_id: str, tags: list[str]) -> None: ...

    @abstractmethod
    async def remove_tags(self, contact_id: str, tags: list[str]) -> None: ...

    @abstractmethod
    async def add_note(self, contact_id: str, body: str) -> None: ...

    @abstractmethod
    async def set_stage(self, contact_id: str, stage: str) -> None:
        """Move the contact's opportunity to a stage named in the client's stage_ids."""


class RecordingGateway(CrmGateway):
    """Keeps every call in memory instead of talking to GHL."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def sent(self) -> list[RenderedMessage]:
        return [c["message"] for c in self.calls if c["call"] == "send_sms"]

    def tags_added(self) -> list[str]:
        return [t for c in self.calls if c["call"] == "add_tags" for t in c["tags"]]

    def notes(self) -> list[str]:
        return [c["body"] for c in self.calls if c["call"] == "add_note"]

    async def send_sms(self, contact_id: str, message: RenderedMessage) -> None:
        if not isinstance(message, RenderedMessage):
            raise TypeError("send_sms only accepts template-rendered messages")
        self.calls.append({"call": "send_sms", "contact_id": contact_id, "message": message})

    async def add_tags(self, contact_id: str, tags: list[str]) -> None:
        self.calls.append({"call": "add_tags", "contact_id": contact_id, "tags": list(tags)})

    async def remove_tags(self, contact_id: str, tags: list[str]) -> None:
        self.calls.append({"call": "remove_tags", "contact_id": contact_id, "tags": list(tags)})

    async def add_note(self, contact_id: str, body: str) -> None:
        self.calls.append({"call": "add_note", "contact_id": contact_id, "body": body})

    async def set_stage(self, contact_id: str, stage: str) -> None:
        self.calls.append({"call": "set_stage", "contact_id": contact_id, "stage": stage})
