"""Read/write conversation state. The only module that opens database sessions."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from booking_engine.models import Conversation, SeenMessage, Turn


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
    ) -> Conversation:
        existing = self.get_conversation(client_slug, contact_id)
        if existing is not None:
            return existing
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
            return conversation

    def save(self, conversation: Conversation) -> Conversation:
        with self._sessions() as session:
            merged = session.merge(conversation)
            session.commit()
            return merged

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
            messages.append({"role": "homeowner", "text": turn.inbound_text})
            if turn.outbound_text:
                messages.append({"role": "engine", "text": turn.outbound_text})
        return messages[-limit:]
