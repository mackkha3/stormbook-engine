"""The turn pipeline: inbound texts → understanding → decision → safe send → record.

Wires the pieces together for one client. No conversation rules live here
(see conversation.py) and no wording (see config/templates.yaml); this module
enforces the cross-cutting guarantees: dedupe, one reply per turn, the
confidence / language / opt-out rules, the message cap, the output safety
check, and "when anything goes wrong, hand off".
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from booking_engine import gateway as gw
from booking_engine.calendar.base import Calendar
from booking_engine.config_loader import ClientConfig, ConfigError
from booking_engine.conversation import (
    DEFAULT_LEAD_SOURCE,
    LEAD_SOURCES,
    OPENER_FRESH,
    OPENER_KEPT,
    REQUIRED_TEMPLATES,
    apply_new_opener,
    decide,
    decide_photo,
)
from booking_engine.debounce import Debouncer
from booking_engine.logger import get
from booking_engine.models import BOOKED, HANDOFF, OPTED_OUT, STOPPED, Conversation
from booking_engine.slots import format_slot, friendly_slot, from_key
from booking_engine.store import Store
from booking_engine.templates import (
    RenderedMessage,
    TemplateError,
    Templates,
    UnsafeOutput,
    static_violation,
)
from booking_engine.turn import Contact, Decision, InboundMessage, Turn, TurnResult, handoff
from booking_engine.understanding import (
    TurnContext,
    Understander,
    Understanding,
    UnderstandingError,
    apply_confidence_rule,
    apply_preference_rule,
    keyword_opt_out,
)

log = get(__name__)

MAX_ENGINE_MESSAGES = 8      # without a booking; the next reply becomes a handoff
NOTE_MESSAGES = 6
PHOTO_TEXT = "[photo]"       # stands in for a picture-only text in history, notes and logs
OPENING_PREFIX = "opening_"   # template_key of a seeded opener turn: opening_<source|from_ghl>
PHOTO_NOTE = (
    "Booking engine: the homeowner sent a photo by text. It is in their GHL conversation."
)


class Engine:
    def __init__(
        self,
        *,
        client: ClientConfig,
        templates: Templates,
        store: Store,
        understander: Understander,
        calendar: Calendar,
        gateway: gw.CrmGateway,
        debounce_seconds: float = 8.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        on_turn: Callable[[TurnResult], None] | None = None,
    ) -> None:
        templates.require(REQUIRED_TEMPLATES)
        too_many = [k for k in REQUIRED_TEMPLATES if templates.slot_count(k) > client.slots_offered]
        if too_many:
            raise ConfigError(
                f"{client.client_slug}: slots_offered is {client.slots_offered} but these "
                f"templates show more times than that: {sorted(too_many)}"
            )
        for name in ("company_name", "company_phone", "service_area_text"):
            violation = static_violation(getattr(client, name))
            if violation:
                raise ConfigError(
                    f"{client.client_slug}: {name} breaks an output rule ({violation}), so "
                    "every message containing it would be blocked"
                )
        self.client, self.templates, self.store = client, templates, store
        self.understander, self.calendar, self.gateway = understander, calendar, gateway
        self._clock = clock
        self._on_turn = on_turn
        self.debouncer = Debouncer(debounce_seconds, self._handle_batch)

    # --- inbound ---------------------------------------------------------

    def receive(self, contact: Contact, message: InboundMessage) -> bool:
        """Accept one inbound text. False if this message id was already seen."""
        if not self.store.register_inbound(self.client.client_slug, message.message_id):
            log.info("duplicate_inbound_ignored", extra={"message_id": message.message_id})
            return False
        self.debouncer.submit(contact.contact_id, (contact, message))
        return True

    async def _handle_batch(self, _key: str, items: list[tuple[Contact, InboundMessage]]) -> None:
        result = await self.handle_turn(items[-1][0], [message for _, message in items])
        if self._on_turn is not None:
            self._on_turn(result)

    # --- one turn --------------------------------------------------------

    async def handle_turn(self, contact: Contact, messages: list[InboundMessage]) -> TurnResult:
        """Process one debounced turn. Sends at most one reply."""
        conv = self.open_conversation(contact)
        bodies = [m.body.strip() for m in messages if m.body.strip()]
        photo = any(m.photo for m in messages)
        text = "\n".join(bodies) or (PHOTO_TEXT if photo else "")
        result = TurnResult(inbound_text=text, status=conv.status, photo=photo)
        try:
            await self._run(conv, bodies, text, result)
        except Exception as exc:
            # Never leave a homeowner in limbo on a bug or an outage: a human takes over.
            log.exception("turn_failed", extra={"contact_id": contact.contact_id})
            conv.status, conv.handoff_reason = HANDOFF, f"internal_error: {type(exc).__name__}"
            result.handoff_reason = conv.handoff_reason
            await self._flag_failure(conv, text)
        self._record(conv, result, [m.message_id for m in messages])
        return result

    async def _flag_failure(self, conv: Conversation, text: str) -> None:
        """Best effort: a turn that blew up must still reach a human through the CRM."""
        note = f"Booking engine handoff. Reason: {conv.handoff_reason}\n\nhomeowner: {text}"
        try:
            await self.gateway.add_tags(conv.contact_id, [gw.TAG_HANDOFF])
            await self.gateway.remove_tags(conv.contact_id, [gw.TAG_ACTIVE])
            await self.gateway.add_note(conv.contact_id, note)
        except Exception:
            log.exception("failure_handoff_not_recorded", extra={"contact_id": conv.contact_id})

    def open_conversation(self, contact: Contact) -> Conversation:
        """Load the contact's conversation, creating it on their first reply.

        The first inbound is always an answer to GHL's opening text, which the engine
        never sent. A new conversation's history is therefore seeded with that text as
        the first business-side message, so the model knows what is being answered.
        """
        conv, created = self.store.get_or_create(
            self.client.client_slug, contact.contact_id, contact.phone, contact.name
        )
        if created:
            key, text = self._seed_opening(conv, contact)
            log.info(
                "conversation_opened",
                extra={"client": self.client.client_slug, "contact_id": contact.contact_id,
                       "lead_source": contact.lead_source, "opening": key, "opening_text": text},
            )
        return conv

    def new_opener(
        self, conv: Conversation, contact: Contact, *, quiet_for: timedelta | None
    ) -> str:
        """GHL sent an existing contact a new opening text. Returns the outcome
        (conversation.OPENER_*); when the conversation goes on, the opener is added
        to its history like a first opener, so the model knows what is being answered."""
        now = self._clock().astimezone(self.client.tz)
        previous = conv.status
        outcome = apply_new_opener(conv, now=now, quiet_for=quiet_for)
        if outcome in (OPENER_FRESH, OPENER_KEPT):
            self.store.save(conv)
            self._seed_opening(conv, contact)
        log.info(
            "new_opener",
            extra={"client": self.client.client_slug, "contact_id": conv.contact_id,
                   "outcome": outcome, "previous_status": previous, "status": conv.status},
        )
        return outcome

    def _seed_opening(self, conv: Conversation, contact: Contact) -> tuple[str, str]:
        key, text = self.opening_message(contact)
        self.store.add_turn(
            conv.id, inbound_text="", inbound_message_ids=[], template_key=key,
            outbound_text=text, status_after=conv.status,
        )
        return key, text

    def _after_opener(self, conv: Conversation) -> bool:
        """True if the last thing in the conversation is an opener (seeded, never sent)."""
        last = self.store.last_turn(conv.id)
        return last is not None and not last.inbound_text and bool(
            last.template_key and last.template_key.startswith(OPENING_PREFIX)
        )

    def opening_message(self, contact: Contact) -> tuple[str, str]:
        """(source key, text) of the opening message this contact was sent.

        Phase 2: the webhook passes the real last outbound message from the GHL
        conversation as `contact.opening_text`; the configured text is the fallback.
        """
        if contact.opening_text and contact.opening_text.strip():
            return f"{OPENING_PREFIX}from_ghl", contact.opening_text.strip()
        source = contact.lead_source if contact.lead_source in LEAD_SOURCES else DEFAULT_LEAD_SOURCE
        if source != contact.lead_source:
            log.warning("unknown_lead_source", extra={"lead_source": contact.lead_source})
        key = f"{OPENING_PREFIX}{source}"
        return key, self.templates.render(key, {"company_name": self.client.company_name}).text

    async def _run(
        self, conv: Conversation, bodies: list[str], text: str, result: TurnResult
    ) -> None:
        tz = self.client.tz
        now = self._clock().astimezone(tz)
        history = self.store.recent_messages(conv.id, NOTE_MESSAGES)
        after_opener = self._after_opener(conv)

        # An opt-out keyword is honoured in every state, even after a handoff or a close.
        opt_out = keyword_opt_out(bodies)
        if conv.status == OPTED_OUT or (conv.status in STOPPED and not opt_out):
            result.ignored = True
            return

        decision: Decision | None = None
        u: Understanding | None = None
        if opt_out:
            u = Understanding(intent="opt_out", confidence=1.0, notes="opt-out keyword (code rule)")
        elif result.photo and not bodies:
            result.intent = "photo"
            decision = await decide_photo(
                Turn(conv=conv, u=None, text=text, client=self.client,
                     calendar=self.calendar, templates=self.templates, now=now,
                     after_opener=after_opener)
            )
        else:
            try:
                u = await self.understander.understand(
                    TurnContext(
                        message=text, now=now, status=conv.status,
                        offered=[from_key(k, tz) for k in conv.current_offer],
                        booked_time=from_key(conv.booked_time, tz) if conv.booked_time else None,
                        awaiting_preference=conv.awaiting_preference, history=history,
                    )
                )
            except UnderstandingError as exc:
                log.error("understanding_failed", extra={"error": str(exc)})
                decision = handoff("understanding_failed")

        if u is not None:
            result.understanding = u.model_dump(mode="json")
            if u.intent != "opt_out":       # opt_out always wins, whatever else is true
                if not u.language.lower().startswith("en"):
                    decision = handoff(f"non_english:{u.language}")
                else:
                    u = apply_preference_rule(apply_confidence_rule(u))
            result.intent = u.intent
            if decision is None:
                decision = await decide(
                    Turn(conv=conv, u=u, text=text, client=self.client,
                         calendar=self.calendar, templates=self.templates, now=now,
                         after_opener=after_opener)
                )

        if (
            decision.template_key and not decision.handoff and not decision.cancelled
            and conv.status not in (BOOKED, *STOPPED)
            and conv.engine_message_count >= MAX_ENGINE_MESSAGES
        ):
            decision = handoff("max_messages_without_booking")

        message, decision = self._render_safely(decision, now)
        await self._apply(conv, decision, message, history, text, result)

    def _render_safely(
        self, decision: Decision, now: datetime
    ) -> tuple[RenderedMessage | None, Decision]:
        """Render the reply and run the output safety check; on failure, hand off instead."""
        if decision.template_key is None:
            return None, decision
        try:
            return self._render(decision, now), decision
        except (UnsafeOutput, TemplateError) as exc:
            reason = exc.reason if isinstance(exc, UnsafeOutput) else str(exc)
            log.error("output_rejected", extra={"template": decision.template_key, "why": reason})
            fallback = handoff(f"output_rejected:{reason}")
            fallback.booking = decision.booking
            try:
                return self._render(fallback, now), fallback
            except (UnsafeOutput, TemplateError):
                fallback.template_key = None
                return None, fallback

    def _render(self, decision: Decision, now: datetime) -> RenderedMessage:
        tz, c = self.client.tz, self.client

        def say(slot: datetime) -> str:
            return friendly_slot(slot, tz, now)

        variables = {
            "company_name": c.company_name,
            "company_phone": c.company_phone,
            "service_area": c.service_area_text,
            **{f"slot_{i}": say(s) for i, s in enumerate(decision.slots, start=1)},
        }
        if decision.booked_time is not None:
            variables["booked_time"] = say(decision.booked_time)
        message = self.templates.render(decision.template_key, variables)
        self.templates.check_output(message, [say(s) for s in decision.verified])
        return message

    async def _apply(
        self, conv: Conversation, decision: Decision, message: RenderedMessage | None,
        history: list[dict[str, str]], text: str, result: TurnResult,
    ) -> None:
        contact_id = conv.contact_id

        async def crm(call: str, **kwargs: Any) -> None:
            await getattr(self.gateway, call)(contact_id, **kwargs)
            logged = {k: (v.text if isinstance(v, RenderedMessage) else v) for k, v in kwargs.items()}
            result.crm_calls.append({"call": call, **logged})

        result.booking = decision.booking
        if decision.reopened:
            await crm("remove_tags", tags=[gw.TAG_CANCELLED])
        if decision.handoff:
            conv.status, conv.handoff_reason = HANDOFF, decision.handoff.reason
            result.handoff_reason = decision.handoff.reason
        if message is not None:
            await crm("send_sms", message=message)
            conv.engine_message_count += 1
            result.template_key, result.reply = message.key, message.text
        if result.photo:
            await crm("add_note", body=PHOTO_NOTE)
        if decision.mark_booked:
            await crm("set_stage", stage="booked")
            await crm("add_tags", tags=[gw.TAG_BOOKED])

        transcript = [*history, {"role": "homeowner", "text": text}]
        if message is not None:
            transcript.append({"role": "engine", "text": message.text})
        lines = "\n".join(f"{m['role']}: {m['text']}" for m in transcript[-NOTE_MESSAGES:])
        if decision.handoff:
            urgent = decision.handoff.urgent
            await crm("add_tags", tags=[gw.TAG_HANDOFF] + ([gw.TAG_EMERGENCY] if urgent else []))
            await crm("remove_tags", tags=[gw.TAG_ACTIVE])
            note = f"Booking engine handoff. Reason: {decision.handoff.reason}"
            await crm("add_note", body=f"{note}\n\n{lines}")
        elif decision.cancelled:
            await crm("add_tags", tags=[gw.TAG_CANCELLED])
            note = "Booking engine: the homeowner cancelled their inspection by text."
            await crm("add_note", body=f"{note}\n\n{lines}")
        elif decision.opted_out:
            still_booked = conv.appointment_id is not None and conv.booked_time is not None
            await crm(
                "add_tags", tags=[gw.TAG_OPTED_OUT] + ([gw.TAG_OPTOUT_BOOKED] if still_booked else [])
            )
            if still_booked:
                when = format_slot(from_key(conv.booked_time, self.client.tz), self.client.tz)
                await crm(
                    "add_note",
                    body=(
                        "Booking engine: this contact opted out of texts and can no longer be "
                        f"texted. Their inspection on {when} is STILL BOOKED and was not "
                        f"cancelled. Call them if you need to reach them.\n\n{lines}"
                    ),
                )

    def _record(self, conv: Conversation, result: TurnResult, message_ids: list[str]) -> None:
        """Persist state + the turn, and emit the structured turn log."""
        self.store.save(conv)
        result.status = conv.status
        self.store.add_turn(
            conv.id, inbound_text=result.inbound_text, inbound_message_ids=message_ids,
            model_output=result.understanding, intent=result.intent,
            template_key=result.template_key, outbound_text=result.reply,
            crm_calls=result.crm_calls, booking_result=result.booking,
            handoff_reason=result.handoff_reason, status_after=conv.status,
        )
        log.info(
            "turn",
            extra={
                "client": self.client.client_slug, "contact_id": conv.contact_id,
                "message_ids": message_ids, "inbound_text": result.inbound_text,
                "model_output": result.understanding, "intent": result.intent,
                "template_key": result.template_key, "outbound_text": result.reply,
                "crm_calls": result.crm_calls, "booking_result": result.booking,
                "handoff_reason": result.handoff_reason, "status": conv.status,
                "ignored": result.ignored,
            },
        )
