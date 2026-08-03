"""Pydantic models for the demo relay.

Deliberately small. This service exists to make one SMS land on a phone
during a Zoom call — it is not the production intake contract.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

E164_PATTERN = r"^\+[1-9]\d{7,14}$"

DamageType = Literal["Hail", "Wind", "Water", "Roof leak"]

Day = Literal[
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


class FireRequest(BaseModel):
    """POST /fire body — sent by apps/demo-form."""

    contractor_first_name: str = Field(min_length=1, max_length=60)
    contractor_company: str = Field(min_length=1, max_length=120)
    homeowner_first_name: str = Field(min_length=1, max_length=60)
    homeowner_phone: str = Field(pattern=E164_PATTERN)
    homeowner_address: str = Field(min_length=1, max_length=200)
    damage_type: DamageType
    slot_a_day: Day
    slot_a_time: str = Field(min_length=1, max_length=12)
    slot_b_day: Day
    slot_b_time: str = Field(min_length=1, max_length=12)

    @field_validator(
        "contractor_first_name",
        "contractor_company",
        "homeowner_first_name",
        "homeowner_address",
        "slot_a_time",
        "slot_b_time",
        mode="before",
    )
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class FireResponse(BaseModel):
    conversation_id: str
    message_sent_at: str
    twilio_message_sid: str
    # Echoed so the operator can see on-screen exactly what the phone received.
    message_body: str


class InboundSMS(BaseModel):
    """Twilio's form-urlencoded inbound webhook, narrowed to what we use."""

    from_number: str = Field(alias="From")
    to_number: str = Field(alias="To")
    body: str = Field(alias="Body", default="")
    message_sid: str | None = Field(alias="MessageSid", default=None)

    model_config = {"populate_by_name": True}


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    uptime_seconds: int
    active_conversations_count: int
    timestamp: str
