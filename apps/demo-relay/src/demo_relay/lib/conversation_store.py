"""In-memory conversation state, keyed by homeowner phone (E.164).

No database. No disk. This is a demo rig — a Railway restart loses state,
and that costs Mack one re-fire of the form. Twilio's message log is the
actual system of record if anyone ever needs an audit trail.

Entries expire 24h after creation; a background sweeper walks the dict
every 5 minutes so a long-lived container doesn't accumulate demos.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from demo_relay.lib.logger import get

log = get(__name__)

TTL_SECONDS = 24 * 60 * 60
SWEEP_INTERVAL_SECONDS = 5 * 60

STATE_AWAITING_REPLY = "awaiting_reply"
STATE_REPLIED = "replied"
STATE_CLOSED = "closed"


class ConversationStore:
    def __init__(self) -> None:
        self._conversations: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def create(self, phone: str, context: dict[str, Any]) -> None:
        async with self._lock:
            record = dict(context)
            record.setdefault("state", STATE_AWAITING_REPLY)
            record["_created_monotonic"] = time.monotonic()
            self._conversations[phone] = record

    async def get(self, phone: str) -> dict[str, Any] | None:
        async with self._lock:
            record = self._conversations.get(phone)
            return dict(record) if record is not None else None

    async def update_state(self, phone: str, new_state: str) -> None:
        async with self._lock:
            record = self._conversations.get(phone)
            if record is None:
                log.warning("conversation_state_update_missing", extra={"phone": phone})
                return
            record["state"] = new_state

    async def set_field(self, phone: str, key: str, value: Any) -> None:
        """Attach extra data to a live conversation (e.g. the Message 2 task ref)."""
        async with self._lock:
            record = self._conversations.get(phone)
            if record is not None:
                record[key] = value

    async def count(self) -> int:
        async with self._lock:
            return len(self._conversations)

    async def cancel_follow_ups(self) -> int:
        """Cancel every armed Message 2 task. Called on shutdown."""
        async with self._lock:
            tasks = [
                record["message_2_task"]
                for record in self._conversations.values()
                if isinstance(record.get("message_2_task"), asyncio.Task)
            ]
        for task in tasks:
            task.cancel()
        return len(tasks)

    async def sweep_expired(self) -> int:
        """Drop conversations older than TTL_SECONDS. Returns count removed."""
        now = time.monotonic()
        async with self._lock:
            stale = [
                phone
                for phone, record in self._conversations.items()
                if now - record.get("_created_monotonic", now) > TTL_SECONDS
            ]
            for phone in stale:
                del self._conversations[phone]
        if stale:
            log.info("conversations_swept", extra={"removed": len(stale)})
        return len(stale)


async def sweeper_loop(store: ConversationStore) -> None:
    """Background task started by the app lifespan. Cancelled on shutdown."""
    while True:
        try:
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            await store.sweep_expired()
        except asyncio.CancelledError:
            raise
        except Exception:
            # A sweeper crash must not take the demo down.
            log.exception("sweeper_error")
