"""The GHL inbound webhook: authenticate, map GHL's payload, gate, hand to the engine.

A GHL workflow ("Customer Replied" → Custom Webhook) posts each homeowner
reply here. This module turns that into `engine.receive(...)`:

1. the shared secret must match (`X-Webhook-Secret` header),
2. the payload is mapped to the internal shape (flat keys, GHL's standard
   webhook keys and `customData` are all accepted),
3. the client is found by location id,
4. the contact is read from GHL: without the `engine-active` tag the reply is
   ignored. If the tag is back on a handed-off contact, a human has returned
   the conversation to the engine and it resumes,
5. on a contact's first reply, the opening text GHL sent is read from the GHL
   conversation so the model knows what is being answered,
6. with the `engine-new-opener` tag, GHL has just sent an existing contact a new
   opener: the conversation starts over or carries on according to its state
   (conversation.apply_new_opener), and the tag is removed.

Payload parsing lives in inbound_payload.py. When a payload carries no usable
message text, the text is read back from the GHL conversation instead.

The reply itself is sent later, after the debounce window, by the engine.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from booking_engine import gateway as gw
from booking_engine.calendar.ghl import GhlCalendar
from booking_engine.config_loader import ClientConfig
from booking_engine.conversation import (
    DEFAULT_LEAD_SOURCE,
    LEAD_SOURCES,
    OPENER_FRESH,
    OPENER_STAYS_HANDED_OFF,
)
from booking_engine.engine import Engine
from booking_engine.ghl_client import GhlClient, GhlError
from booking_engine.inbound_payload import Inbound
from booking_engine.logger import get
from booking_engine.models import ACTIVE, BOOKED, HANDOFF, Conversation
from booking_engine.settings import Settings, secret_from_env
from booking_engine.store import Store
from booking_engine.templates import Templates
from booking_engine.turn import Contact, InboundMessage
from booking_engine.understanding import Understander, keyword_opt_out

log = get(__name__)


@dataclass
class ClientRuntime:
    client: ClientConfig
    engine: Engine
    ghl: GhlClient
    gateway: gw.CrmGateway


# --- GHL conversation lookups --------------------------------------------------

def _is_sms(message: dict[str, Any]) -> bool:
    return "SMS" in str(message.get("messageType") or "").upper()


def _text(message: dict[str, Any]) -> str:
    return str(message.get("body") or "").strip()


def _has_photo(message: dict[str, Any]) -> bool:
    return bool(message.get("attachments"))


def find_message(messages: list[dict[str, Any]], body: str) -> dict[str, Any] | None:
    """The newest inbound message with this text."""
    for message in reversed(messages):
        if message.get("direction") == "inbound" and _text(message) == body.strip():
            return message
    return None


def newest_inbound(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The homeowner's latest text: words, a picture, or both."""
    for message in reversed(messages):
        if message.get("direction") == "inbound" and _is_sms(message) and (
            _text(message) or _has_photo(message)
        ):
            return message
    return None


def find_opening(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The last text the business sent: on a first reply, that is GHL's opening message."""
    for message in reversed(messages):
        if message.get("direction") == "outbound" and _is_sms(message) and _text(message):
            return message
    return None


def find_opening_text(messages: list[dict[str, Any]]) -> str | None:
    opening = find_opening(messages)
    return _text(opening) if opening else None


def _sent_at(message: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(message.get("dateAdded")))
    except ValueError:
        return None


def quiet_before(messages: list[dict[str, Any]], opening: dict[str, Any]) -> timedelta | None:
    """How long the conversation had no texts before `opening`. None if it can't be told.

    Every text counts, including a person's from the GHL inbox: those never reach the
    engine. Calls do not, so the missed call that triggered the opener is no texting.
    """
    earlier = [m for m in messages[: messages.index(opening)] if _is_sms(m)]
    if not earlier:
        return timedelta.max
    opened, last = _sent_at(opening), _sent_at(earlier[-1])
    if opened is None or last is None:
        return None
    return opened - last


def _fallback_message_id(inbound: Inbound) -> str:
    """Stable id for a payload without one: same contact + text in the same minute."""
    minute = (inbound.timestamp or datetime.now(UTC)).strftime("%Y%m%d%H%M")
    digest = hashlib.sha256(f"{inbound.contact_id}|{inbound.body}|{minute}".encode()).hexdigest()
    return f"h-{digest[:40]}"


# --- wiring + the inbound flow -------------------------------------------------

def build_runtimes(
    *,
    settings: Settings,
    clients: dict[str, ClientConfig],
    templates: Templates,
    store: Store,
    understander: Understander,
    http: httpx.AsyncClient,
) -> dict[str, ClientRuntime]:
    """One engine per client, keyed by GHL location id, all on the real GHL backends."""
    runtimes: dict[str, ClientRuntime] = {}
    for location_id, client in clients.items():
        ghl = GhlClient(
            http, token=secret_from_env(client.ghl_token_env), location_id=location_id
        )
        gateway = gw.GhlGateway(ghl, client)
        engine = Engine(
            client=client, templates=templates, store=store, understander=understander,
            calendar=GhlCalendar(ghl, client), gateway=gateway,
            debounce_seconds=settings.debounce_seconds,
        )
        runtimes[location_id] = ClientRuntime(client, engine, ghl, gateway)
    return runtimes


def _ignored(reason: str, inbound: Inbound) -> dict[str, str]:
    log.info(
        "inbound_ignored",
        extra={"reason": reason, "location_id": inbound.location_id,
               "contact_id": inbound.contact_id},
    )
    return {"status": "ignored", "reason": reason}


async def _reactivate(rt: ClientRuntime, conv: Conversation) -> None:
    """A human put `engine-active` back on a handed-off contact: the engine resumes."""
    still_booked = conv.appointment_id is not None and conv.booked_time is not None
    conv.status = BOOKED if still_booked else ACTIVE
    conv.handoff_reason = None
    conv.current_offer = []              # whatever was on the table is stale by now
    conv.offer_template = None
    conv.awaiting_preference = False
    conv.unclear_streak = conv.none_work_streak = conv.engine_message_count = 0
    rt.engine.store.save(conv)
    log.info(
        "conversation_reactivated",
        extra={"client": rt.client.client_slug, "contact_id": conv.contact_id},
    )
    try:
        # Clear the alert tags so the next handoff re-triggers the GHL alert workflow.
        await rt.gateway.remove_tags(conv.contact_id, [gw.TAG_HANDOFF, gw.TAG_EMERGENCY])
    except GhlError as exc:
        log.error("handoff_tags_not_cleared", extra={"error": str(exc)})


async def handle_inbound(runtimes: dict[str, ClientRuntime], inbound: Inbound) -> dict[str, str]:
    """Gate one inbound reply and queue it for the engine. Raises GhlError if GHL is down."""
    rt = runtimes.get(inbound.location_id)
    if rt is None:
        return _ignored("unknown_location", inbound)

    contact = await rt.ghl.get_contact(inbound.contact_id)
    if contact.get("locationId") not in (None, rt.client.ghl_location_id):
        return _ignored("contact_not_in_location", inbound)
    tags = {str(tag).strip().lower() for tag in contact.get("tags") or []}
    slug = rt.client.client_slug
    conv = rt.engine.store.get_conversation(slug, inbound.contact_id)

    messages: list[dict[str, Any]] | None = None
    matched: dict[str, Any] | None = None
    if not inbound.body:
        # No usable text in the payload: read what they sent from GHL itself. It is
        # either a picture with no words or a text the workflow could not pass on.
        messages = await rt.ghl.recent_messages(inbound.contact_id)
        matched = newest_inbound(messages)
        if matched is None:
            return _ignored("empty_message", inbound)
        inbound = replace(
            inbound, message_id=str(matched.get("id") or "") or None, body=_text(matched)
        )

    new_opener = gw.TAG_ACTIVE in tags and gw.TAG_NEW_OPENER in tags
    if gw.TAG_ACTIVE not in tags:
        # Not ours to answer. An opt-out keyword is still recorded for a contact we know.
        if conv is None or not keyword_opt_out([inbound.body]):
            return _ignored("not_engine_active", inbound)
    elif conv is not None and conv.status == HANDOFF and not new_opener:
        await _reactivate(rt, conv)

    message_id, opening = inbound.message_id, None
    if conv is None or message_id is None or new_opener:
        if messages is None:
            try:
                messages = await rt.ghl.recent_messages(inbound.contact_id)
            except GhlError as exc:
                log.warning("ghl_messages_unavailable", extra={"error": str(exc)})
                messages = []
        matched = matched or find_message(messages, inbound.body)
        message_id = message_id or (matched and str(matched.get("id") or "")) or None
        if conv is None or new_opener:
            opening = find_opening(messages)

    name = str(contact.get("name") or "").strip() or " ".join(
        str(contact.get(k) or "").strip() for k in ("firstName", "lastName")
    ).strip()
    person = Contact(
        contact_id=inbound.contact_id,
        phone=str(contact.get("phone") or inbound.phone),
        name=name or inbound.name,
        lead_source=(
            inbound.lead_source if inbound.lead_source in LEAD_SOURCES else DEFAULT_LEAD_SOURCE
        ),
        opening_text=_text(opening) if opening else None,
    )
    outcome = None
    if new_opener:
        outcome = await _new_opener(rt, conv, person, messages or [], opening)
        if outcome == OPENER_STAYS_HANDED_OFF and not keyword_opt_out([inbound.body]):
            return _ignored("handoff_not_reopened", inbound)

    accepted = rt.engine.receive(
        person,
        InboundMessage(
            message_id=message_id or _fallback_message_id(inbound),
            body=inbound.body, timestamp=inbound.timestamp,
            photo=matched is not None and _has_photo(matched),
        ),
    )
    if not accepted:
        return {"status": "duplicate"}
    if conv is None or outcome == OPENER_FRESH:
        await rt.gateway.set_stage(inbound.contact_id, "replied")     # their first reply
    return {"status": "accepted"}


async def _new_opener(
    rt: ClientRuntime, conv: Conversation | None, person: Contact,
    messages: list[dict[str, Any]], opening: dict[str, Any] | None,
) -> str | None:
    """Apply a new GHL opener once, then take the `engine-new-opener` tag off.

    Returns the outcome (conversation.OPENER_*), or None when there was nothing to
    apply: no conversation yet (the opener is seeded as on any first reply), the
    opener was already applied (a second text of a burst, or the tag could not be
    removed last time), or it cannot be found in GHL (the tag stays, for next time).
    """
    store, slug = rt.engine.store, rt.client.client_slug
    if opening is None:
        log.warning("new_opener_not_found", extra={"contact_id": person.contact_id})
        return None
    if conv is not None and any(
        m["role"] == "engine" and m["text"] == _text(opening)
        for m in store.recent_messages(conv.id)
    ):
        opening = None       # the newest outbound is the engine's own reply, not an opener
    opener_id = opening and str(opening.get("id") or opening.get("dateAdded") or "")
    # Recorded like an inbound message id: applied once, even with two webhooks racing.
    first_time = bool(opener_id) and store.register_inbound(slug, f"opener:{opener_id}")
    outcome = None
    if conv is not None and first_time:
        outcome = rt.engine.new_opener(conv, person, quiet_for=quiet_before(messages, opening))
    remove = [gw.TAG_NEW_OPENER]
    if outcome == OPENER_FRESH:
        # The old outcome no longer describes them; the next handoff alerts again.
        remove += [gw.TAG_HANDOFF, gw.TAG_EMERGENCY, gw.TAG_CANCELLED]
    elif outcome == OPENER_STAYS_HANDED_OFF:
        remove += [gw.TAG_ACTIVE]    # the opener workflow re-added it; a person still owns this
    try:
        await rt.gateway.remove_tags(person.contact_id, remove)
    except GhlError as exc:
        log.error("new_opener_tag_not_removed", extra={"error": str(exc)})
    return outcome
