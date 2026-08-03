"""10-minute follow-up (Message 2) via a fire-and-forget asyncio task.

No Celery, no cron, no queue. One `asyncio.sleep` per demo conversation on
a service that handles a handful of conversations a week.

The task re-reads conversation state immediately before sending: if the
homeowner replied inside the window, the state is `replied` and Message 2
is skipped. That check is the whole point of the task — without it the
prospect watching the demo would see a nudge arrive after they'd already
booked.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from demo_relay import templates
from demo_relay.lib.conversation_store import STATE_AWAITING_REPLY, ConversationStore
from demo_relay.lib.logger import get
from demo_relay.lib.twilio_client import TwilioConfig, send_sms

log = get(__name__)

DEFAULT_DELAY_SECONDS = 600


def delay_seconds() -> int:
    """Follow-up delay. Overridable via env so a rehearsal can use 30s."""
    raw = os.environ.get("MESSAGE_2_DELAY_SECONDS", "").strip()
    if not raw:
        return DEFAULT_DELAY_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        log.warning("invalid_message_2_delay", extra={"value": raw})
        return DEFAULT_DELAY_SECONDS


async def run_message_2(
    phone: str,
    *,
    store: ConversationStore,
    http: httpx.AsyncClient,
    twilio: TwilioConfig,
    delay: int,
) -> str | None:
    """Sleep, re-check state, send Message 2. Returns the SID, or None if skipped."""
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        log.info("message_2_cancelled", extra={"phone": phone})
        raise

    record: dict[str, Any] | None = await store.get(phone)
    if record is None:
        log.info("message_2_skipped", extra={"phone": phone, "reason": "conversation_gone"})
        return None
    if record.get("state") != STATE_AWAITING_REPLY:
        log.info(
            "message_2_skipped",
            extra={
                "phone": phone,
                "conversation_id": record.get("conversation_id"),
                "reason": "state_changed",
                "state": record.get("state"),
            },
        )
        return None

    try:
        body = templates.render_message_2(record)
        sid = await send_sms(
            phone,
            body,
            from_number=twilio.from_number,
            account_sid=twilio.account_sid,
            auth_token=twilio.auth_token,
            http=http,
        )
    except Exception:
        # A failed nudge must not take down the app.
        log.exception(
            "message_2_failed",
            extra={"phone": phone, "conversation_id": record.get("conversation_id")},
        )
        return None

    log.info(
        "message_2_sent",
        extra={
            "phone": phone,
            "conversation_id": record.get("conversation_id"),
            "twilio_message_sid": sid,
        },
    )
    return sid


def schedule_message_2(
    phone: str,
    *,
    store: ConversationStore,
    http: httpx.AsyncClient,
    twilio: TwilioConfig,
    delay: int | None = None,
) -> asyncio.Task:
    """Kick off the follow-up. Caller keeps the returned task only to cancel it."""
    task = asyncio.create_task(
        run_message_2(
            phone,
            store=store,
            http=http,
            twilio=twilio,
            delay=delay_seconds() if delay is None else delay,
        ),
        name=f"message-2:{phone}",
    )
    return task
