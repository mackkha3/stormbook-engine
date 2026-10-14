"""Everything the engine does to the CRM: send the SMS, tags, notes, pipeline stage.

`RecordingGateway` keeps the calls in memory (simulator + tests); `GhlGateway`
makes them for real through ghl_client. `send_sms` takes a RenderedMessage,
never a bare string, so only template output can be sent.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from booking_engine.config_loader import ClientConfig
from booking_engine.ghl_client import GhlClient, GhlError
from booking_engine.logger import get
from booking_engine.templates import RenderedMessage

log = get(__name__)

TAG_ACTIVE = "engine-active"
TAG_HANDOFF = "engine-handoff"
TAG_EMERGENCY = "engine-emergency"
TAG_BOOKED = "booked"
TAG_OPTED_OUT = "engine-opted-out"
TAG_OPTOUT_BOOKED = "engine-optout-booked"   # opted out but still has an inspection booked
TAG_CANCELLED = "engine-cancelled"
TAG_NEW_OPENER = "engine-new-opener"         # set by GHL with each opener; the engine removes it


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


class GhlGateway(CrmGateway):
    """The real thing: one client's GHL sub-account."""

    def __init__(self, ghl: GhlClient, client: ClientConfig) -> None:
        self._ghl = ghl
        self._client = client

    async def send_sms(self, contact_id: str, message: RenderedMessage) -> None:
        if not isinstance(message, RenderedMessage):
            raise TypeError("send_sms only accepts template-rendered messages")
        await self._ghl.send_sms(contact_id, message.text)

    async def add_tags(self, contact_id: str, tags: list[str]) -> None:
        await self._ghl.add_tags(contact_id, tags)

    async def remove_tags(self, contact_id: str, tags: list[str]) -> None:
        await self._ghl.remove_tags(contact_id, tags)

    async def add_note(self, contact_id: str, body: str) -> None:
        await self._ghl.add_note(contact_id, body)

    async def set_stage(self, contact_id: str, stage: str) -> None:
        """Move the contact's opportunity in the client's pipeline.

        The pipeline board is bookkeeping: a missing opportunity or a failed move is
        logged and skipped, never allowed to derail a turn that already texted the
        homeowner.
        """
        pipeline_id = self._client.pipeline_id
        stage_id = getattr(self._client.stage_ids, stage)
        try:
            opportunity_id = await self._ghl.find_opportunity(contact_id, pipeline_id)
            if opportunity_id is None:
                log.warning(
                    "no_opportunity_for_contact",
                    extra={"contact_id": contact_id, "pipeline_id": pipeline_id, "stage": stage},
                )
                return
            await self._ghl.update_opportunity_stage(
                opportunity_id, pipeline_id=pipeline_id, stage_id=stage_id
            )
        except GhlError as exc:
            log.error(
                "stage_move_failed",
                extra={"contact_id": contact_id, "stage": stage, "error": str(exc)},
            )
