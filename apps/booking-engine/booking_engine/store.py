"""Read/write conversation state. The only module that opens database sessions."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from booking_engine.models import OPTED_OUT, Conversation, SeenMessage, Turn


class OptOutProtected(Exception):
    """The conversation is an opt-out record and must not be deleted."""


class Store:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def register_inbound(self, client_slug: str, message_id: str) -> bool:
        """Record an inbound message id. False if it was already seen (a webhook retry)."""
        with self._sessions() as session:
            session.add(SeenMessage(client_slug=client_slug, message_id=message_id))
            try:
                session.commit()
            except IntegrityError:
                return False
        return True

    def get_conversation(self, client_slug: str, contact_id: str) -> Conversation | None:
        with self._sessions() as session:
            return session.scalar(
                select(Conversation).where(
                    Conversation.client_slug == client_slug,
                    Conversation.contact_id == contact_id,
                )
            )

    def get_or_create(
        self, client_slug: str, contact_id: str, phone: str, name: str | None
    ) -> tuple[Conversation, bool]:
        """Return (conversation, created)."""
        existing = self.get_conversation(client_slug, contact_id)
        if existing is not None:
            return existing, False
        with self._sessions() as session:
            conversation = Conversation(
                client_slug=client_slug,
                contact_id=contact_id,
                contact_phone=phone,
                contact_name=name,
                current_offer=[],
                offered_slots=[],
            )
            session.add(conversation)
            session.commit()
            return conversation, True

    def save(self, conversation: Conversation) -> Conversation:
        with self._sessions() as session:
            merged = session.merge(conversation)
            session.commit()
            return merged

    def reset_contact(self, client_slug: str, contact_id: str) -> dict[str, Any] | None:
        """Delete one contact's conversation and its turns, so their next text starts fresh.

        Returns what was removed, or None if the contact has no conversation. An
        opted-out conversation is the record that this number must never be texted
        again: it is never deleted (raises OptOutProtected). Seen message ids are
        kept, so a late webhook retry of an old text is still dropped.
        """
        with self._sessions() as session:
            conversation = session.scalar(
                select(Conversation).where(
                    Conversation.client_slug == client_slug,
                    Conversation.contact_id == contact_id,
                )
            )
            if conversation is None:
                return None
            if conversation.status == OPTED_OUT:
                raise OptOutProtected(f"{client_slug}/{contact_id} is opted out")
            removed = {
                "previous_status": conversation.status,
                "appointment_id": conversation.appointment_id,
                "booked_time": conversation.booked_time,
            }
            turns = session.execute(
                delete(Turn).where(Turn.conversation_id == conversation.id)
            ).rowcount
            session.delete(conversation)
            session.commit()
            return {**removed, "turns_deleted": turns}

    def add_turn(self, conversation_id: int, **fields: Any) -> Turn:
        with self._sessions() as session:
            turn = Turn(conversation_id=conversation_id, **fields)
            session.add(turn)
            session.commit()
            return turn

    def turns(self, conversation_id: int) -> list[Turn]:
        with self._sessions() as session:
            return list(
                session.scalars(
                    select(Turn).where(Turn.conversation_id == conversation_id).order_by(Turn.id)
                )
            )

    def last_turn(self, conversation_id: int) -> Turn | None:
        with self._sessions() as session:
            return session.scalar(
                select(Turn).where(Turn.conversation_id == conversation_id)
                .order_by(Turn.id.desc()).limit(1)
            )

    def recent_messages(self, conversation_id: int, limit: int = 6) -> list[dict[str, str]]:
        """The last `limit` messages, oldest first, as {role, text}."""
        with self._sessions() as session:
            recent = session.scalars(
                select(Turn)
                .where(Turn.conversation_id == conversation_id)
                .order_by(Turn.id.desc())
                .limit(limit)
            ).all()
        messages: list[dict[str, str]] = []
        for turn in reversed(recent):
            if turn.inbound_text:      # the opening-message row has no homeowner text
                messages.append({"role": "homeowner", "text": turn.inbound_text})
            if turn.outbound_text:
                messages.append({"role": "engine", "text": turn.outbound_text})
        return messages[-limit:]
