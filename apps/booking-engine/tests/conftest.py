"""Test fixtures. No test here touches the network: the model is scripted,
the calendar is the mock, and the CRM is the in-memory recorder.

The clock is frozen at Monday 2026-10-05 09:00 America/Chicago and the mock
calendar has every working-hour slot free, so with a 2-hour lead time the
first offer is always `Mon 10/5 at 11:00 AM` and `Tue 10/6 at 12:00 PM`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from booking_engine.calendar.mock import MockCalendar
from booking_engine.config_loader import load_client
from booking_engine.db import init_db, make_engine
from booking_engine.engine import Contact, Engine, InboundMessage, TurnResult
from booking_engine.gateway import RecordingGateway
from booking_engine.store import Store
from booking_engine.templates import Templates
from booking_engine.understanding import TurnContext, Understanding

TZ = ZoneInfo("America/Chicago")
TEST_LEAD_MINUTES = 120     # pinned, so the scenarios above stay put whatever the client sets
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=TZ)
SLOT_1 = datetime(2026, 10, 5, 11, 0, tzinfo=TZ)
SLOT_2 = datetime(2026, 10, 6, 12, 0, tzinfo=TZ)
FIXTURES = yaml.safe_load(
    (Path(__file__).parent / "fixtures" / "homeowner_messages.yaml").read_text(encoding="utf-8")
)


def U(intent: str, confidence: float = 0.95, **fields) -> Understanding:
    return Understanding(intent=intent, confidence=confidence, **fields)


class ScriptedUnderstander:
    """Returns queued answers instead of calling the model."""

    def __init__(self) -> None:
        self.queue: list[Understanding | Exception] = []
        self.contexts: list[TurnContext] = []
        self.delay = 0.0

    async def understand(self, ctx: TurnContext) -> Understanding:
        self.contexts.append(ctx)
        if self.delay:
            await asyncio.sleep(self.delay)
        answer = self.queue.pop(0) if self.queue else U("affirm")
        if isinstance(answer, Exception):
            raise answer
        return answer


class Harness:
    def __init__(self, *, debounce_seconds: float = 0.0, **client_overrides) -> None:
        self.client = load_client("roof-demo").model_copy(
            update={"min_lead_time_minutes": TEST_LEAD_MINUTES, **client_overrides}
        )
        self.templates = Templates.load()
        self.store = Store(init_db(make_engine("sqlite:///:memory:")))
        self.calendar = MockCalendar(TZ, busy_ratio=0.0)
        self.gateway = RecordingGateway()
        self.understander = ScriptedUnderstander()
        self.now = NOW
        self.contact = Contact(contact_id="contact-1", phone="+19035550199", name="Jane Doe")
        self.results: list[TurnResult] = []
        self._ids = 0
        self.engine = Engine(
            client=self.client, templates=self.templates, store=self.store,
            understander=self.understander, calendar=self.calendar, gateway=self.gateway,
            debounce_seconds=debounce_seconds, clock=lambda: self.now,
            on_turn=self.results.append,
        )

    def message(self, body: str, message_id: str | None = None) -> InboundMessage:
        self._ids += 1
        return InboundMessage(message_id=message_id or f"msg-{self._ids}", body=body)

    async def say(self, body: str, understanding: Understanding | Exception | None = None):
        """One homeowner turn, with the model's answer scripted."""
        if understanding is not None:
            self.understander.queue.append(understanding)
        return await self.engine.handle_turn(self.contact, [self.message(body)])

    async def reach(self, state: str) -> None:
        """Drive the conversation into a named starting state."""
        if state == "fresh":
            return
        await self.say("yes", U("affirm"))                       # → slots offered
        if state in ("booked", "cancelled"):
            await self.say("1", U("pick_slot", slot_choice=1))
            if state == "cancelled":
                await self.say("please cancel my inspection", U("cancel"))
        elif state == "asked":                                   # → ask_preference sent
            await self.say("neither", U("none_work"))
            await self.say("no", U("none_work"))
        self.gateway.calls.clear()

    @property
    def conv(self):
        return self.store.get_conversation(self.client.client_slug, self.contact.contact_id)

    @property
    def sent(self) -> list[str]:
        return [m.text for m in self.gateway.sent]


@pytest.fixture
def h() -> Harness:
    return Harness()
