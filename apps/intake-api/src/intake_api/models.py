"""Request/response Pydantic models.

The intake payload shape is locked — it must match what
`apps/intake-form/form.js` POSTs. Changing this contract is a
coordinated change with the form.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["meta", "angi", "direct", "web"]
DamageType = Literal["Hail", "Wind", "Water", "Not sure"]


class IntakePayload(BaseModel):
    client_id: str = Field(min_length=1)
    source: Source
    first_name: str = Field(min_length=2)
    last_name: str = Field(min_length=2)
    phone: str  # E.164 expected — defensively re-normalized server-side
    email: str | None = None  # form already validates shape; keep loose to avoid losing leads
    address: str = Field(min_length=1)
    damage_type: DamageType
    photo_count: int = Field(ge=0, le=5)
    submitted_at: datetime
    # A2P: the SMS consent checkbox. Defaults to False so any source that
    # doesn't carry it is treated as not consenting (see lib/ghl.py).
    sms_consent: bool = False
    sms_consent_at: datetime | None = None


class IntakeResponse(BaseModel):
    lead_id: str
    ghl_contact_id: str
    request_id: str


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    uptime_seconds: int
    timestamp: str
