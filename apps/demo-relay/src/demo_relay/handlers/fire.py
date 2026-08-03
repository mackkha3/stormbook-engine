"""POST /fire — the button Mack presses on a Zoom call.

Composes Message 1, sends it through Twilio, stores the conversation so an
inbound reply can be correlated, and arms the 10-minute follow-up. The
response echoes the exact message body so the operator's screen shows what
the phone in his hand just received.

No auth: the endpoint lives behind a screen share for the length of three
pilot sales. If the URL ever leaks, add DEMO_ACCESS_TOKEN + a Bearer check.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

from demo_relay import templates
from demo_relay.lib.conversation_store import STATE_AWAITING_REPLY
from demo_relay.lib.logger import get
from demo_relay.lib.scheduler import schedule_message_2
from demo_relay.lib.twilio_client import TwilioConfig, TwilioConfigError, TwilioSendError, send_sms
from demo_relay.models import FireRequest, FireResponse

router = APIRouter()

log = get(__name__)


@router.post("/fire", response_model=FireResponse)
async def fire(payload: FireRequest, request: Request) -> FireResponse:
    request_id = getattr(request.state, "request_id", "")
    conversation_id = str(uuid.uuid4())

    try:
        twilio = TwilioConfig.from_env()
    except TwilioConfigError as exc:
        log.error("twilio_config_missing", extra={"request_id": request_id, "error": str(exc)})
        raise HTTPException(status_code=500, detail=f"Twilio not configured — {exc}") from exc

    context = payload.model_dump()
    body = templates.render_message_1(context)

    log.info(
        "fire_received",
        extra={
            "request_id": request_id,
            "conversation_id": conversation_id,
            "to": payload.homeowner_phone,
            "contractor_company": payload.contractor_company,
        },
    )

    try:
        message_sid = await send_sms(
            payload.homeowner_phone,
            body,
            from_number=twilio.from_number,
            account_sid=twilio.account_sid,
            auth_token=twilio.auth_token,
            http=request.app.state.http,
        )
    except TwilioSendError as exc:
        log.error(
            "fire_send_failed",
            extra={
                "request_id": request_id,
                "conversation_id": conversation_id,
                "to": payload.homeowner_phone,
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=502, detail=f"Twilio send failed — {exc}") from exc

    sent_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    store = request.app.state.store
    await store.create(
        payload.homeowner_phone,
        {
            "conversation_id": conversation_id,
            **context,
            "message_1_sent_at": sent_at,
            "message_1_sid": message_sid,
            "message_2_scheduled": True,
            "state": STATE_AWAITING_REPLY,
        },
    )

    task = schedule_message_2(
        payload.homeowner_phone,
        store=store,
        http=request.app.state.http,
        twilio=twilio,
    )
    # Keeping the handle lets a future revision cancel the nudge outright;
    # today the state re-check inside the task is what actually suppresses it.
    await store.set_field(payload.homeowner_phone, "message_2_task", task)

    log.info(
        "fire_sent",
        extra={
            "request_id": request_id,
            "conversation_id": conversation_id,
            "to": payload.homeowner_phone,
            "twilio_message_sid": message_sid,
        },
    )

    return FireResponse(
        conversation_id=conversation_id,
        message_sent_at=sent_at,
        twilio_message_sid=message_sid,
        message_body=body,
    )
