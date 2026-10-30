from __future__ import annotations

import pytest


VALID_PAYLOAD = {
    "client_id": "henderson-roofing",
    "source": "web",
    "first_name": "Pat",
    "last_name": "Homeowner",
    "phone": "+19035550199",
    "email": "pat@example.com",
    "address": "123 Elm St, Tyler, TX 75701",
    "damage_type": "Hail",
    "photo_count": 0,
    "submitted_at": "2026-06-13T15:00:00Z",
}


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert isinstance(body["uptime_seconds"], int)


@pytest.mark.asyncio
async def test_valid_intake_creates_contact(client, httpx_mock):
    httpx_mock.add_response(
        url="https://services.leadconnectorhq.com/contacts/",
        method="POST",
        status_code=201,
        json={"contact": {"id": "ghl_contact_42"}},
    )
    resp = await client.post("/intake", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ghl_contact_id"] == "ghl_contact_42"
    assert body["lead_id"]
    assert body["request_id"]


@pytest.mark.asyncio
async def test_invalid_intake_returns_422(client):
    bad = dict(VALID_PAYLOAD)
    bad["first_name"] = "A"  # min_length=2 violation
    resp = await client.post("/intake", json=bad)
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["request_id"]
    assert body["detail"]["errors"]


@pytest.mark.asyncio
async def test_unknown_client_returns_404(client):
    bad = dict(VALID_PAYLOAD, client_id="not-a-real-client")
    resp = await client.post("/intake", json=bad)
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "CLIENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_ghl_retries_then_succeeds(client, httpx_mock, monkeypatch):
    # Speed up retry backoff for the test
    monkeypatch.setattr("intake_api.lib.ghl.DEFAULT_DELAYS_MS", (1, 1, 1))

    httpx_mock.add_response(
        url="https://services.leadconnectorhq.com/contacts/",
        method="POST",
        status_code=500,
    )
    httpx_mock.add_response(
        url="https://services.leadconnectorhq.com/contacts/",
        method="POST",
        status_code=502,
    )
    httpx_mock.add_response(
        url="https://services.leadconnectorhq.com/contacts/",
        method="POST",
        status_code=201,
        json={"contact": {"id": "ghl_contact_retry_win"}},
    )

    resp = await client.post("/intake", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    assert resp.json()["ghl_contact_id"] == "ghl_contact_retry_win"


@pytest.mark.asyncio
async def test_ghl_unrecoverable_after_retries(client, httpx_mock, monkeypatch):
    monkeypatch.setattr("intake_api.lib.ghl.DEFAULT_DELAYS_MS", (1, 1, 1))
    for _ in range(4):  # 1 initial + 3 retries = 4 total
        httpx_mock.add_response(
            url="https://services.leadconnectorhq.com/contacts/",
            method="POST",
            status_code=500,
        )
    resp = await client.post("/intake", json=VALID_PAYLOAD)
    assert resp.status_code == 502
    body = resp.json()
    assert body["code"] == "GHL_UNAVAILABLE"
    assert body["request_id"]


@pytest.mark.asyncio
async def test_request_id_header_round_trip(client, httpx_mock):
    httpx_mock.add_response(
        url="https://services.leadconnectorhq.com/contacts/",
        method="POST",
        status_code=201,
        json={"contact": {"id": "ghl_contact_id"}},
    )
    resp = await client.post(
        "/intake",
        json=VALID_PAYLOAD,
        headers={"X-Request-ID": "test-req-id-1234"},
    )
    assert resp.status_code == 200
    assert resp.headers["X-Request-ID"] == "test-req-id-1234"
    assert resp.json()["request_id"] == "test-req-id-1234"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extra", "expect_dnd"),
    [
        ({}, True),  # a form that predates the checkbox
        ({"sms_consent": False, "sms_consent_at": "2026-06-13T15:00:00Z"}, True),
        ({"sms_consent": True, "sms_consent_at": "2026-06-13T15:00:00Z"}, False),
    ],
)
async def test_intake_only_allows_sms_with_consent(client, httpx_mock, extra, expect_dnd):
    import json

    httpx_mock.add_response(
        url="https://services.leadconnectorhq.com/contacts/",
        method="POST",
        status_code=201,
        json={"contact": {"id": "ghl_contact_consent"}},
    )
    resp = await client.post("/intake", json=dict(VALID_PAYLOAD, **extra))
    assert resp.status_code == 200

    sent = json.loads(httpx_mock.get_request(method="POST").content)
    assert ("dndSettings" in sent) is expect_dnd
    assert ("sms-consent-no" in sent["tags"]) is expect_dnd
    assert ("sms-consent-yes" in sent["tags"]) is not expect_dnd
