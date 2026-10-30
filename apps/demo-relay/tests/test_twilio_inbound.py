from __future__ import annotations

import logging
import os

import httpx
import pytest

from demo_relay.lib.conversation_store import STATE_CLOSED, STATE_REPLIED
from demo_relay.lib.twilio_sig import build_signature
from tests.conftest import FIRE_PAYLOAD, TWILIO_MESSAGES_URL

HOMEOWNER = "+19035550199"
INBOUND_URL = "http://test/twilio/inbound"


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


def _signature(data: dict[str, str], url: str = INBOUND_URL) -> str:
    return build_signature(os.environ["TWILIO_AUTH_TOKEN"], url, data)


async def _post(client, data: dict[str, str]) -> httpx.Response:
    return await client.post(
        "/twilio/inbound", data=data, headers={"X-Twilio-Signature": _signature(data)}
    )


def _sent_body(httpx_mock) -> str:
    request = httpx_mock.get_request(url=TWILIO_MESSAGES_URL, method="POST")
    return dict(httpx.QueryParams(request.content.decode()))["Body"]


@pytest.mark.asyncio
async def test_slot_a_reply_sends_message_3a(client, app, httpx_mock):
    await _seed(app)
    httpx_mock.add_response(
        url=TWILIO_MESSAGES_URL, method="POST", json={"sid": "SM_3a"}, status_code=201
    )

    resp = await _post(client, _inbound("Tuesday"))

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

    await _post(client, _inbound("thurs works better"))

    assert "booked for Thursday at 2:00 PM" in _sent_body(httpx_mock)


@pytest.mark.asyncio
async def test_wrong_number_reply_sends_message_3d_and_closes(client, app, httpx_mock):
    await _seed(app)
    httpx_mock.add_response(
        url=TWILIO_MESSAGES_URL, method="POST", json={"sid": "SM_3d"}, status_code=201
    )

    await _post(client, _inbound("wrong number"))

    assert "sorry for the confusion" in _sent_body(httpx_mock)
    record = await app.state.store.get(HOMEOWNER)
    assert record["state"] == STATE_CLOSED


@pytest.mark.asyncio
async def test_any_reply_marks_conversation_replied(client, app, httpx_mock):
    await _seed(app)
    httpx_mock.add_response(
        url=TWILIO_MESSAGES_URL, method="POST", json={"sid": "SM_3a"}, status_code=201
    )

    await _post(client, _inbound("Tuesday"))

    record = await app.state.store.get(HOMEOWNER)
    assert record["state"] == STATE_REPLIED


@pytest.mark.asyncio
async def test_unmatched_reply_sends_nothing(client, app, httpx_mock):
    await _seed(app)

    resp = await _post(client, _inbound("How much will this cost?"))

    assert resp.status_code == 200
    # No Twilio response registered — an outbound send would have raised.
    assert httpx_mock.get_requests() == []
    record = await app.state.store.get(HOMEOWNER)
    assert record["state"] == STATE_REPLIED


@pytest.mark.asyncio
async def test_ambiguous_reply_sends_nothing(client, app, httpx_mock):
    await _seed(app)

    resp = await _post(client, _inbound("either works"))

    assert resp.status_code == 200
    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_unknown_phone_returns_200_and_sends_nothing(client, httpx_mock):
    resp = await _post(
        client,
        {"From": "+19998887777", "To": "+19035550100", "Body": "Tuesday"},
    )

    assert resp.status_code == 200
    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_response_is_empty_twiml(client, app, httpx_mock):
    await _seed(app)

    resp = await _post(client, _inbound("no thanks"))

    assert resp.headers["content-type"].startswith("application/xml")
    assert resp.text.endswith("<Response></Response>")


@pytest.mark.asyncio
async def test_missing_from_field_is_a_422(client):
    resp = await _post(client, {"Body": "Tuesday"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_valid_signature_processes_message(client, app, httpx_mock):
    await _seed(app)
    httpx_mock.add_response(
        url=TWILIO_MESSAGES_URL, method="POST", json={"sid": "SM_3a"}, status_code=201
    )

    resp = await _post(client, _inbound("Tuesday"))

    assert resp.status_code == 200
    assert "booked for Tuesday" in _sent_body(httpx_mock)


@pytest.mark.asyncio
async def test_invalid_signature_returns_403(client, app, httpx_mock):
    await _seed(app)
    before = dict(await app.state.store.get(HOMEOWNER))

    resp = await client.post(
        "/twilio/inbound",
        data=_inbound("Tuesday"),
        headers={"X-Twilio-Signature": "not-the-signature"},
    )

    assert resp.status_code == 403
    assert resp.json() == {"detail": "Invalid signature"}
    assert httpx_mock.get_requests() == []
    assert await app.state.store.get(HOMEOWNER) == before


@pytest.mark.asyncio
async def test_invalid_signature_logs_source_ip_and_request_id(client, caplog):
    await client.post(
        "/twilio/inbound",
        data=_inbound("Tuesday"),
        headers={"X-Request-ID": "req-1", "X-Forwarded-For": "203.0.113.9, 10.0.0.1"},
    )

    record = next(r for r in caplog.records if r.getMessage() == "twilio_signature_invalid")
    assert record.source_ip == "203.0.113.9"
    assert record.request_id == "req-1"


@pytest.mark.asyncio
async def test_dev_mode_bypass_logs_warning(client, app, httpx_mock, monkeypatch, caplog):
    monkeypatch.setenv("TWILIO_SIGNATURE_VERIFICATION", "off")
    await _seed(app)
    httpx_mock.add_response(
        url=TWILIO_MESSAGES_URL, method="POST", json={"sid": "SM_3a"}, status_code=201
    )

    resp = await client.post("/twilio/inbound", data=_inbound("Tuesday"))

    assert resp.status_code == 200
    assert "booked for Tuesday" in _sent_body(httpx_mock)
    assert any(
        r.getMessage() == "twilio_signature_check_disabled" and r.levelno == logging.WARNING
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_missing_signature_returns_403(client, app, httpx_mock):
    await _seed(app)

    resp = await client.post("/twilio/inbound", data=_inbound("Tuesday"))

    assert resp.status_code == 403
    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_missing_auth_token_fails_closed(client, app, httpx_mock, monkeypatch):
    await _seed(app)
    data = _inbound("Tuesday")
    signature = build_signature("", INBOUND_URL, data)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "")

    resp = await client.post(
        "/twilio/inbound", data=data, headers={"X-Twilio-Signature": signature}
    )

    assert resp.status_code == 403
    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_signature_covers_undeclared_twilio_params(client, app, httpx_mock):
    await _seed(app)
    httpx_mock.add_response(
        url=TWILIO_MESSAGES_URL, method="POST", json={"sid": "SM_3a"}, status_code=201
    )
    data = {**_inbound("Tuesday"), "NumMedia": "0", "AccountSid": "ACtest", "SmsStatus": "received"}

    resp = await _post(client, data)

    assert resp.status_code == 200
    assert httpx_mock.get_requests() != []


@pytest.mark.asyncio
async def test_signature_uses_forwarded_proto_and_host(client, app, httpx_mock):
    await _seed(app)
    httpx_mock.add_response(
        url=TWILIO_MESSAGES_URL, method="POST", json={"sid": "SM_3a"}, status_code=201
    )
    data = _inbound("Tuesday")
    public_url = "https://relay.example.com/twilio/inbound?client=henderson"

    resp = await client.post(
        "/twilio/inbound?client=henderson",
        data=data,
        headers={
            "X-Twilio-Signature": _signature(data, public_url),
            "X-Forwarded-Proto": "https,http",
            "X-Forwarded-Host": "relay.example.com",
        },
    )

    assert resp.status_code == 200
    assert httpx_mock.get_requests() != []


@pytest.mark.asyncio
async def test_unsigned_malformed_request_returns_403_not_422(client):
    resp = await client.post("/twilio/inbound", data={"Body": "Tuesday"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_send_failure_still_returns_200_and_keeps_the_conversation_open(
    client, app, httpx_mock, caplog
):
    await _seed(app)
    httpx_mock.add_response(url=TWILIO_MESSAGES_URL, method="POST", status_code=400, json={})

    resp = await _post(client, _inbound("wrong number"))

    # A non-200 would make Twilio replay the inbound.
    assert resp.status_code == 200
    assert resp.text.endswith("<Response></Response>")
    record = await app.state.store.get(HOMEOWNER)
    assert record["state"] == STATE_REPLIED
    failure = next(r for r in caplog.records if r.getMessage() == "inbound_reply_failed")
    assert failure.branch == "WRONG_NUMBER"
