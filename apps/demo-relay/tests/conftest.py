"""Test fixtures.

Env vars are set before app import so TwilioConfig.from_env() resolves
without a .env file. No test ever reaches the real Twilio API — pytest-httpx
intercepts httpx's real transport; the ASGITransport used by `client` is
left alone.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("TWILIO_ACCOUNT_SID", "ACtestsid0000000000000000000000000")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "test_auth_token")
os.environ.setdefault("TWILIO_FROM_NUMBER", "+19035550100")
os.environ.setdefault("ENVIRONMENT", "development")

TWILIO_MESSAGES_URL = (
    f"https://api.twilio.com/2010-04-01/Accounts/{os.environ['TWILIO_ACCOUNT_SID']}/Messages.json"
)

FIRE_PAYLOAD = {
    "contractor_first_name": "Mike",
    "contractor_company": "Henderson Roofing",
    "homeowner_first_name": "Jane",
    "homeowner_phone": "+19035550199",
    "homeowner_address": "123 Elm St",
    "damage_type": "Hail",
    "slot_a_day": "Tuesday",
    "slot_a_time": "10:00 AM",
    "slot_b_day": "Thursday",
    "slot_b_time": "2:00 PM",
}


@pytest.fixture
def app():
    from demo_relay.main import app as _app

    return _app


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        yield ac


