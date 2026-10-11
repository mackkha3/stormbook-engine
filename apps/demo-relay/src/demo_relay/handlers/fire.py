"""POST /fire — the button Mack presses on a Zoom call.

Composes Message 1, sends it through Twilio, stores the conversation so an
inbound reply can be correlated, and arms the 10-minute follow-up. The
response echoes the exact message body so the operator's screen shows what
the phone in his hand just received.

Auth: a bearer token, so the operator can park this on a public URL and hit
it from an iPad mid-conversation without anyone who finds the domain being
able to burn the Twilio balance. Set DEMO_ACCESS_TOKEN to arm it; leave it
unset locally and the check disappears.
"""

from __future__ import annotations

import hmac
import os
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


def _client_ip(request: Request) -> str:
    # Railway sits behind a proxy; the first XFF hop is the real caller.
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _check_token(request: Request, request_id: str) -> None:
    """401 unless the request carries the expected bearer token.

    An unset or empty DEMO_ACCESS_TOKEN disables the check entirely — that's
    the local-dev path, and keeping it frictionless is the point.
    """
    expected = os.environ.get("DEMO_ACCESS_TOKEN", "").strip()
    if not expected:
        return

    header = request.headers.get("Authorization", "")
    scheme, _, presented = header.partition(" ")

    if not header or scheme.lower() != "bearer" or not presented.strip():
        _log_auth_failure(request, request_id, "missing")
        raise HTTPException(status_code=401, detail="Missing bearer token")

    if not hmac.compare_digest(presented.strip(), expected):
        _log_auth_failure(request, request_id, "invalid")
        raise HTTPException(status_code=401, detail="Invalid token")


def _log_auth_failure(request: Request, request_id: str, reason: str) -> None:
    # `reason` is a fixed label, never the header or the token itself.
    log.warning(
        "fire_auth_failed",
        extra={
            "request_id": request_id,
            "source_ip": _client_ip(request),
            "reason": reason,
        },
    )


@router.post("/fire", response_model=FireResponse)
async def fire(payload: FireRequest, request: Request) -> FireResponse:
    request_id = getattr(request.state, "request_id", "")

    _check_token(request, request_id)

    conversation_id = str(uuid.uuid4())
    received_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # A2P: no consent, no SMS. Checked before anything touches Twilio, and
    # logged either way so every submission has a consent record.
    log.info(
        "fire_consent",
        extra={
            "request_id": request_id,
            "conversation_id": conversation_id,
            "to": payload.homeowner_phone,
            "sms_consent": payload.sms_consent,
            "sms_consent_at": payload.sms_consent_at,
            "received_at": received_at,
        },
    )
    if not payload.sms_consent:
        raise HTTPException(status_code=403, detail="The demo text requires SMS consent.")

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
            "sms_consent_recorded_at": received_at,
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
