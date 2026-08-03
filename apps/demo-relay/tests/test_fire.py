from __future__ import annotations

import asyncio

import httpx
import pytest

from demo_relay.lib import scheduler
from demo_relay.lib.conversation_store import STATE_AWAITING_REPLY, STATE_REPLIED, ConversationStore
from demo_relay.lib.twilio_client import TwilioConfig
from tests.conftest import FIRE_PAYLOAD, TWILIO_MESSAGES_URL


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["active_conversations_count"] == 0
    assert isinstance(body["uptime_seconds"], int)


@pytest.mark.asyncio
async def test_fire_returns_expected_fields(client, httpx_mock):
    httpx_mock.add_response(
        url=TWILIO_MESSAGES_URL, method="POST", json={"sid": "SM_test_1"}, status_code=201
    )

    resp = await client.post("/fire", json=FIRE_PAYLOAD)

    assert resp.status_code == 200
    body = resp.json()
    assert body["twilio_message_sid"] == "SM_test_1"
    assert body["conversation_id"]
    assert body["message_sent_at"].endswith("Z")
    assert body["message_body"].startswith("Hi Jane, this is Mike with Henderson Roofing.")
    assert resp.headers["X-Request-ID"]


@pytest.mark.asyncio
async def test_fire_sends_message_1_to_the_homeowner(client, httpx_mock):
    httpx_mock.add_response(
        url=TWILIO_MESSAGES_URL, method="POST", json={"sid": "SM_test_2"}, status_code=201
    )

    await client.post("/fire", json=FIRE_PAYLOAD)

    sent = httpx_mock.get_request(url=TWILIO_MESSAGES_URL, method="POST")
    form = dict(httpx.QueryParams(sent.content.decode()))
    assert form["To"] == "+19035550199"
    assert form["From"] == "+19035550100"
    assert "about the hail damage" in form["Body"]


@pytest.mark.asyncio
async def test_fire_stores_conversation_and_arms_message_2(client, app, httpx_mock):
    httpx_mock.add_response(
        url=TWILIO_MESSAGES_URL, method="POST", json={"sid": "SM_test_3"}, status_code=201
    )

    resp = await client.post("/fire", json=FIRE_PAYLOAD)
    conversation_id = resp.json()["conversation_id"]

    record = await app.state.store.get("+19035550199")
    assert record is not None
    assert record["conversation_id"] == conversation_id
    assert record["state"] == STATE_AWAITING_REPLY
    assert record["message_2_scheduled"] is True
    assert record["slot_a_day"] == "Tuesday"
    assert record["message_1_sent_at"].endswith("Z")

    task = record["message_2_task"]
    assert isinstance(task, asyncio.Task)
    assert not task.done()

    health = await client.get("/health")
    assert health.json()["active_conversations_count"] == 1


@pytest.mark.asyncio
async def test_fire_rejects_non_e164_phone(client):
    resp = await client.post("/fire", json={**FIRE_PAYLOAD, "homeowner_phone": "903-555-0199"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["request_id"]


@pytest.mark.asyncio
async def test_fire_rejects_unknown_damage_type(client):
    resp = await client.post("/fire", json={**FIRE_PAYLOAD, "damage_type": "Meteor"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_fire_returns_502_when_twilio_rejects(client, httpx_mock):
    httpx_mock.add_response(
        url=TWILIO_MESSAGES_URL,
        method="POST",
        status_code=400,
        json={"message": "The 'To' number is not a valid phone number."},
    )

    resp = await client.post("/fire", json=FIRE_PAYLOAD)

    assert resp.status_code == 502
    assert "Twilio send failed" in resp.json()["detail"]


# --- Message 2 scheduling ---------------------------------------------------


@pytest.mark.asyncio
async def test_message_2_fires_when_still_awaiting_reply(httpx_mock):
    httpx_mock.add_response(
        url=TWILIO_MESSAGES_URL, method="POST", json={"sid": "SM_followup"}, status_code=201
    )
    store = ConversationStore()
    await store.create("+19035550199", {**FIRE_PAYLOAD, "conversation_id": "c1"})

    async with httpx.AsyncClient() as http:
        sid = await scheduler.run_message_2(
            "+19035550199",
            store=store,
            http=http,
            twilio=TwilioConfig.from_env(),
            delay=0,
        )

    assert sid == "SM_followup"
    sent = httpx_mock.get_request(url=TWILIO_MESSAGES_URL, method="POST")
    body = dict(httpx.QueryParams(sent.content.decode()))["Body"]
    assert body.startswith("Jane — Mike here again.")


@pytest.mark.asyncio
async def test_message_2_skipped_after_a_reply(httpx_mock):
    store = ConversationStore()
    await store.create("+19035550199", {**FIRE_PAYLOAD, "conversation_id": "c2"})
    await store.update_state("+19035550199", STATE_REPLIED)

    async with httpx.AsyncClient() as http:
        sid = await scheduler.run_message_2(
            "+19035550199",
            store=store,
            http=http,
            twilio=TwilioConfig.from_env(),
            delay=0,
        )

    assert sid is None
    # No response was registered — a send here would have raised.
    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_message_2_skipped_when_conversation_expired():
    store = ConversationStore()
    async with httpx.AsyncClient() as http:
        sid = await scheduler.run_message_2(
            "+19035559999",
            store=store,
            http=http,
            twilio=TwilioConfig.from_env(),
            delay=0,
        )
    assert sid is None


@pytest.mark.asyncio
async def test_scheduler_delay_reads_env(monkeypatch):
    monkeypatch.setenv("MESSAGE_2_DELAY_SECONDS", "30")
    assert scheduler.delay_seconds() == 30
    monkeypatch.setenv("MESSAGE_2_DELAY_SECONDS", "not-a-number")
    assert scheduler.delay_seconds() == scheduler.DEFAULT_DELAY_SECONDS
    monkeypatch.delenv("MESSAGE_2_DELAY_SECONDS")
    assert scheduler.delay_seconds() == 600
