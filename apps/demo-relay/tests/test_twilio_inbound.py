from __future__ import annotations

import httpx
import pytest

from demo_relay.lib.conversation_store import STATE_CLOSED, STATE_REPLIED
from tests.conftest import FIRE_PAYLOAD, TWILIO_MESSAGES_URL

HOMEOWNER = "+19035550199"


async def _seed(app, **overrides):
    await app.state.store.create(
        HOMEOWNER,
        {**FIRE_PAYLOAD, "conversation_id": "conv-test", **overrides},
    )


def _inbound(body: str) -> dict[str, str]:
    return {
        "From": HOMEOWNER,
        "To": "+19035550100",
        "Body": body,
        "MessageSid": "SM_inbound_1",
    }


def _sent_body(httpx_mock) -> str:
    request = httpx_mock.get_request(url=TWILIO_MESSAGES_URL, method="POST")
    return dict(httpx.QueryParams(request.content.decode()))["Body"]


@pytest.mark.asyncio
async def test_slot_a_reply_sends_message_3a(client, app, httpx_mock):
    await _seed(app)
    httpx_mock.add_response(
        url=TWILIO_MESSAGES_URL, method="POST", json={"sid": "SM_3a"}, status_code=201
    )

    resp = await client.post("/twilio/inbound", data=_inbound("Tuesday"))

    assert resp.status_code == 200
    body = _sent_body(httpx_mock)
    assert "booked for Tuesday at 10:00 AM, 123 Elm St" in body
    assert body.endswith("— Mike, Henderson Roofing")


@pytest.mark.asyncio
async def test_slot_b_reply_confirms_the_second_slot(client, app, httpx_mock):
    await _seed(app)
    httpx_mock.add_response(
        url=TWILIO_MESSAGES_URL, method="POST", json={"sid": "SM_3a"}, status_code=201
    )

    await client.post("/twilio/inbound", data=_inbound("thurs works better"))

    assert "booked for Thursday at 2:00 PM" in _sent_body(httpx_mock)


@pytest.mark.asyncio
async def test_wrong_number_reply_sends_message_3d_and_closes(client, app, httpx_mock):
    await _seed(app)
    httpx_mock.add_response(
        url=TWILIO_MESSAGES_URL, method="POST", json={"sid": "SM_3d"}, status_code=201
    )

    await client.post("/twilio/inbound", data=_inbound("wrong number"))

    assert "sorry for the confusion" in _sent_body(httpx_mock)
    record = await app.state.store.get(HOMEOWNER)
    assert record["state"] == STATE_CLOSED


@pytest.mark.asyncio
async def test_any_reply_marks_conversation_replied(client, app, httpx_mock):
    await _seed(app)
    httpx_mock.add_response(
        url=TWILIO_MESSAGES_URL, method="POST", json={"sid": "SM_3a"}, status_code=201
    )

    await client.post("/twilio/inbound", data=_inbound("Tuesday"))

    record = await app.state.store.get(HOMEOWNER)
    assert record["state"] == STATE_REPLIED


@pytest.mark.asyncio
async def test_unmatched_reply_sends_nothing(client, app, httpx_mock):
    await _seed(app)

    resp = await client.post("/twilio/inbound", data=_inbound("How much will this cost?"))

    assert resp.status_code == 200
    # No Twilio response registered — an outbound send would have raised.
    assert httpx_mock.get_requests() == []
    record = await app.state.store.get(HOMEOWNER)
    assert record["state"] == STATE_REPLIED


@pytest.mark.asyncio
async def test_ambiguous_reply_sends_nothing(client, app, httpx_mock):
    await _seed(app)

    resp = await client.post("/twilio/inbound", data=_inbound("either works"))

    assert resp.status_code == 200
    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_unknown_phone_returns_200_and_sends_nothing(client, httpx_mock):
    resp = await client.post(
        "/twilio/inbound",
        data={"From": "+19998887777", "To": "+19035550100", "Body": "Tuesday"},
    )

    assert resp.status_code == 200
    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_response_is_empty_twiml(client, app, httpx_mock):
    await _seed(app)

    resp = await client.post("/twilio/inbound", data=_inbound("no thanks"))

    assert resp.headers["content-type"].startswith("application/xml")
    assert resp.text.endswith("<Response></Response>")


@pytest.mark.asyncio
async def test_missing_from_field_is_a_422(client):
    resp = await client.post("/twilio/inbound", data={"Body": "Tuesday"})
    assert resp.status_code == 422
