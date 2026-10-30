"""POST /twilio/inbound — the homeowner's reply comes back here.

Twilio POSTs form-urlencoded (not JSON). A request without a valid
X-Twilio-Signature gets a 403 and is never processed. Every authentic request
gets 200 with an empty TwiML document: a non-200 makes Twilio retry, and any
TwiML content would make Twilio send its own auto-reply on top of ours.

Only Branches 1 and 3 are wired. Everything else is UNMATCHED and we stay
silent — Mack covers those verbally: "and here Jane could say a different
time, and Mike takes over."
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response

from demo_relay import templates
from demo_relay.lib.branch_matcher import classify, is_ambiguous
from demo_relay.lib.conversation_store import STATE_CLOSED, STATE_REPLIED
from demo_relay.lib.logger import get
from demo_relay.lib.twilio_client import TwilioConfig, TwilioConfigError, TwilioSendError, send_sms
from demo_relay.lib.twilio_sig import verify_signature

router = APIRouter()

log = get(__name__)

EMPTY_TWIML = "<?xml version='1.0' encoding='UTF-8'?><Response></Response>"


def _twiml() -> Response:
    return Response(content=EMPTY_TWIML, media_type="application/xml")


def _first_value(header: str | None) -> str:
    """Proxies may chain forwarded headers as a comma-separated list."""
    return (header or "").split(",")[0].strip()


def _public_url(request: Request) -> str:
    """Rebuild the URL Twilio signed; behind Railway the app sees internal http."""
    proto = _first_value(request.headers.get("X-Forwarded-Proto")) or request.url.scheme
    host = _first_value(request.headers.get("X-Forwarded-Host")) or request.url.netloc
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{proto}://{host}{request.url.path}{query}"


async def _require_twilio_signature(request: Request) -> None:
    request_id = getattr(request.state, "request_id", "")

    if os.environ.get("TWILIO_SIGNATURE_VERIFICATION", "").strip().lower() == "off":
        log.warning("twilio_signature_check_disabled", extra={"request_id": request_id})
        return

    # Every POSTed field is signed, including the many we don't declare below.
    form = await request.form()
    params = {key: value for key, value in form.multi_items() if isinstance(value, str)}

    if not verify_signature(
        auth_token=os.environ.get("TWILIO_AUTH_TOKEN", "").strip(),
        url=_public_url(request),
        params=params,
        signature_header=request.headers.get("X-Twilio-Signature"),
    ):
        source_ip = _first_value(request.headers.get("X-Forwarded-For")) or (
            request.client.host if request.client else ""
        )
        log.warning(
            "twilio_signature_invalid",
            extra={"request_id": request_id, "source_ip": source_ip},
        )
        raise HTTPException(status_code=403, detail="Invalid signature")


@router.post("/twilio/inbound", dependencies=[Depends(_require_twilio_signature)])
async def twilio_inbound(
    request: Request,
    From: str = Form(...),
    To: str = Form(""),
    Body: str = Form(""),
    MessageSid: str = Form(""),
) -> Response:
    request_id = getattr(request.state, "request_id", "")
    store = request.app.state.store

    record = await store.get(From)
    if record is None:
        log.warning(
            "inbound_unknown_conversation",
            extra={"request_id": request_id, "from": From, "twilio_message_sid": MessageSid},
        )
        return _twiml()

    conversation_id = record.get("conversation_id")
    slot_a_day = record.get("slot_a_day", "")
    slot_b_day = record.get("slot_b_day", "")

    match = classify(Body, slot_a_day=slot_a_day, slot_b_day=slot_b_day)

    # Any reply at all suppresses the 10-minute nudge.
    await store.update_state(From, STATE_REPLIED)

    log.info(
        "inbound_classified",
        extra={
            "request_id": request_id,
            "conversation_id": conversation_id,
            "from": From,
            "branch": match.branch,
            "matched_keyword": match.matched_keyword,
            "body": Body,
        },
    )

    if match.branch == "UNMATCHED":
        if is_ambiguous(Body, slot_a_day=slot_a_day, slot_b_day=slot_b_day):
            # Branch 2 territory. Logged so we know how often it happens
            # before deciding whether it's worth building.
            log.info(
                "inbound_ambiguous",
                extra={
                    "request_id": request_id,
                    "conversation_id": conversation_id,
                    "body": Body,
                },
            )
        return _twiml()

    if match.branch == "YES_SLOT_A":
        reply = templates.render_message_3a(
            record,
            chosen_day=record.get("slot_a_day", ""),
            chosen_time=record.get("slot_a_time", ""),
        )
    elif match.branch == "YES_SLOT_B":
        reply = templates.render_message_3a(
            record,
            chosen_day=record.get("slot_b_day", ""),
            chosen_time=record.get("slot_b_time", ""),
        )
    else:  # WRONG_NUMBER
        reply = templates.render_message_3d(record)

    try:
        twilio = TwilioConfig.from_env()
        sid = await send_sms(
            From,
            reply,
            from_number=twilio.from_number,
            account_sid=twilio.account_sid,
            auth_token=twilio.auth_token,
            http=request.app.state.http,
        )
    except (TwilioConfigError, TwilioSendError):
        # Still 200 — a retry from Twilio would just re-classify the same reply.
        log.exception(
            "inbound_reply_failed",
            extra={
                "request_id": request_id,
                "conversation_id": conversation_id,
                "branch": match.branch,
            },
        )
        return _twiml()

    if match.branch == "WRONG_NUMBER":
        await store.update_state(From, STATE_CLOSED)

    log.info(
        "inbound_reply_sent",
        extra={
            "request_id": request_id,
            "conversation_id": conversation_id,
            "branch": match.branch,
            "twilio_message_sid": sid,
        },
    )
    return _twiml()
