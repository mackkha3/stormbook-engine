"""Database models: conversations, their turns, and seen inbound message ids."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Conversation.status values (spec section 8).
ACTIVE = "active"                # replying, nothing offered yet
SLOTS_OFFERED = "slots_offered"  # times are on the table (see current_offer)
BOOKED = "booked"                # appointment exists (appointment_id, booked_time)
HANDOFF = "handoff"              # a human owns it; the engine stays silent
OPTED_OUT = "opted_out"          # never text again
CLOSED = "closed"                # not interested; the engine stays silent
CANCELLED = "cancelled"          # homeowner cancelled their booking; they may text to rebook

STOPPED = {HANDOFF, OPTED_OUT, CLOSED}


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("client_slug", "contact_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_slug: Mapped[str] = mapped_column(String(64), index=True)
    contact_id: Mapped[str] = mapped_column(String(128), index=True)
    contact_phone: Mapped[str] = mapped_column(String(32), default="")
    contact_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=ACTIVE)

    # Slot times are stored as UTC ISO strings (slots.slot_key).
    current_offer: Mapped[list] = mapped_column(JSON, default=list)   # on the table now, in order
    offered_slots: Mapped[list] = mapped_column(JSON, default=list)   # every time ever offered
    offer_template: Mapped[str | None] = mapped_column(String(64), nullable=True)
    awaiting_preference: Mapped[bool] = mapped_column(default=False)
    none_work_streak: Mapped[int] = mapped_column(Integer, default=0)
    unclear_streak: Mapped[int] = mapped_column(Integer, default=0)
    engine_message_count: Mapped[int] = mapped_column(Integer, default=0)

    appointment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    booked_time: Mapped[str | None] = mapped_column(String(40), nullable=True)
    handoff_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class Turn(Base):
    """One debounced homeowner turn and everything the engine did about it."""

    __tablename__ = "turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    inbound_text: Mapped[str] = mapped_column(Text)
    inbound_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    model_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    template_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outbound_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    crm_calls: Mapped[list] = mapped_column(JSON, default=list)
    booking_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    handoff_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status_after: Mapped[str] = mapped_column(String(20), default=ACTIVE)


class SeenMessage(Base):
    """Inbound message ids already accepted, so webhook retries are dropped."""

    __tablename__ = "seen_messages"

    client_slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
