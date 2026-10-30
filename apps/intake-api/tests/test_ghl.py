from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from intake_api.lib import retry
from intake_api.lib.client_config import ClientConfig
from intake_api.lib.errors import ApiException
from intake_api.lib.geocode import GeocodeResult
from intake_api.lib.ghl import (
    GHL_BASE_URL,
    GHL_CONTACTS_PATH,
    _safe_text,
    build_contact_payload,
    create_contact,
)
from intake_api.models import IntakePayload

GHL_URL = GHL_BASE_URL + GHL_CONTACTS_PATH


def _payload() -> IntakePayload:
    return IntakePayload(
        client_id="henderson-roofing",
        source="meta",
        first_name="Pat",
        last_name="Homeowner",
        phone="+19035550199",
        email="pat@example.com",
        address="123 Elm St, Tyler, TX 75701",
        damage_type="Hail",
        photo_count=0,
        submitted_at=datetime(2026, 6, 13, 15, 0, tzinfo=UTC),
    )


def _client() -> ClientConfig:
    return ClientConfig(
        client_id="henderson-roofing",
        ghl_location_id="loc_abc123",
        ghl_api_key="key_xyz",
        custom_field_ids={
            "damage_type": "cf_damage",
            "stormbook_lead_id": "cf_sblid",
            "property_address": "cf_addr",
            "sms_consent": "cf_consent",
            "sms_consent_at": "cf_consent_at",
        },
        service_area_zips=["75701"],
    )


def test_build_contact_payload_maps_core_fields():
    body = build_contact_payload(
        _payload(),
        client=_client(),
        stormbook_lead_id="lead-uuid-1",
        geocode_result=None,
    )
    assert body["locationId"] == "loc_abc123"
    assert body["firstName"] == "Pat"
    assert body["lastName"] == "Homeowner"
    assert body["phone"] == "+19035550199"
    assert body["email"] == "pat@example.com"
    assert body["address1"] == "123 Elm St, Tyler, TX 75701"
    assert "source-meta" in body["tags"]
    assert "roofing-storm" in body["tags"]


def test_build_contact_payload_custom_fields():
    body = build_contact_payload(
        _payload(),
        client=_client(),
        stormbook_lead_id="lead-uuid-2",
        geocode_result=None,
    )
    by_id = {cf["id"]: cf["value"] for cf in body["customFields"]}
    assert by_id["cf_damage"] == "Hail"
    assert by_id["cf_sblid"] == "lead-uuid-2"
    assert by_id["cf_addr"] == "123 Elm St, Tyler, TX 75701"


def test_build_contact_payload_omits_email_when_none():
    p = _payload().model_copy(update={"email": None})
    body = build_contact_payload(
        p, client=_client(), stormbook_lead_id="x", geocode_result=None
    )
    assert "email" not in body


def test_build_contact_payload_includes_geocode_when_available():
    g = GeocodeResult(
        formatted_address="123 Elm St, Tyler, TX 75701, USA",
        lat=32.35,
        lng=-95.30,
        state="TX",
        zip_code="75701",
    )
    body = build_contact_payload(
        _payload(), client=_client(), stormbook_lead_id="x", geocode_result=g
    )
    assert body["state"] == "TX"
    assert body["postalCode"] == "75701"


def test_build_contact_payload_without_consent_blocks_sms():
    body = build_contact_payload(
        _payload(), client=_client(), stormbook_lead_id="x", geocode_result=None
    )
    assert body["dndSettings"]["SMS"]["status"] == "active"
    assert "sms-consent-no" in body["tags"]
    assert "sms-consent-yes" not in body["tags"]
    by_id = {cf["id"]: cf["value"] for cf in body["customFields"]}
    assert by_id["cf_consent"] == "false"
    # No explicit consent timestamp → falls back to the submission time.
    assert by_id["cf_consent_at"] == "2026-06-13T15:00:00+00:00"


def test_build_contact_payload_with_consent_allows_sms():
    consented_at = datetime(2026, 6, 13, 14, 59, 30, tzinfo=UTC)
    p = _payload().model_copy(update={"sms_consent": True, "sms_consent_at": consented_at})
    body = build_contact_payload(
        p, client=_client(), stormbook_lead_id="x", geocode_result=None
    )
    assert "dndSettings" not in body
    assert "sms-consent-yes" in body["tags"]
    assert "sms-consent-no" not in body["tags"]
    by_id = {cf["id"]: cf["value"] for cf in body["customFields"]}
    assert by_id["cf_consent"] == "true"
    assert by_id["cf_consent_at"] == "2026-06-13T14:59:30+00:00"


@pytest.fixture
def no_sleep(monkeypatch):
    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(retry.asyncio, "sleep", fake_sleep)


async def _create(lead_id: str = "lead-uuid-9") -> str:
    async with httpx.AsyncClient() as http:
        return await create_contact(
            _payload(),
            client_cfg=_client(),
            stormbook_lead_id=lead_id,
            request_id="req-1",
            http=http,
        )


def _log_record(caplog, event: str):
    return next(r for r in caplog.records if r.getMessage() == event)


@pytest.mark.asyncio
async def test_retries_exhausted_raises_502_and_logs_the_full_lead(httpx_mock, no_sleep, caplog):
    for _ in range(4):
        httpx_mock.add_exception(httpx.ConnectError("connection refused"))

    with pytest.raises(ApiException) as excinfo:
        await _create()

    assert excinfo.value.status_code == 502
    assert excinfo.value.code == "GHL_UNAVAILABLE"
    assert excinfo.value.detail == {"stormbook_lead_id": "lead-uuid-9", "reason": "network"}
    assert len(httpx_mock.get_requests()) == 4
    record = _log_record(caplog, "ghl_unrecoverable")
    assert record.stormbook_lead_id == "lead-uuid-9"
    assert record.payload["phone"] == "+19035550199"
    assert record.payload["address"] == "123 Elm St, Tyler, TX 75701"
    assert "connection refused" in record.error


@pytest.mark.asyncio
async def test_timeout_is_retried_until_ghl_answers(httpx_mock, no_sleep):
    httpx_mock.add_exception(httpx.ReadTimeout("slow"))
    httpx_mock.add_response(url=GHL_URL, status_code=201, json={"contact": {"id": "c-1"}})

    assert await _create() == "c-1"


@pytest.mark.asyncio
async def test_non_transport_exception_is_not_retried(httpx_mock, no_sleep):
    httpx_mock.add_exception(ValueError("our bug"))

    with pytest.raises(ValueError):
        await _create()

    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.asyncio
async def test_ghl_4xx_raises_502_once_and_logs_ghl_rejected(httpx_mock, no_sleep, caplog):
    httpx_mock.add_response(url=GHL_URL, status_code=422, json={"message": "bad phone"})

    with pytest.raises(ApiException) as excinfo:
        await _create()

    assert excinfo.value.status_code == 502
    assert excinfo.value.detail["stormbook_lead_id"] == "lead-uuid-9"
    assert excinfo.value.detail["ghl_status"] == 422
    # 4xx means our payload was wrong, so a retry can't help.
    assert len(httpx_mock.get_requests()) == 1
    record = _log_record(caplog, "ghl_rejected")
    assert record.status == 422
    assert "bad phone" in record.body
    assert not [r for r in caplog.records if r.getMessage() == "ghl_unrecoverable"]


@pytest.mark.asyncio
async def test_intake_caller_gets_502_when_ghl_rejects(client, httpx_mock):
    httpx_mock.add_response(url=GHL_URL, status_code=400, json={"message": "nope"})

    resp = await client.post(
        "/intake",
        json={**_payload().model_dump(mode="json"), "source": "web"},
    )

    assert resp.status_code == 502
    assert resp.json()["code"] == "GHL_UNAVAILABLE"
    assert resp.json()["detail"]["stormbook_lead_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"text": "<html>not json</html>"}, ""),
        ({"json": ["unexpected"]}, ""),
        ({"json": {"contact": "unexpected"}}, ""),
        ({"json": {"contactId": "c-flat"}}, "c-flat"),
        ({"json": {"contact": {"contactId": "c-alt"}}}, "c-alt"),
    ],
)
async def test_contact_id_extraction_tolerates_odd_success_bodies(httpx_mock, kwargs, expected):
    httpx_mock.add_response(url=GHL_URL, status_code=201, **kwargs)

    assert await _create() == expected


def test_safe_text_survives_an_unreadable_body():
    class Unreadable:
        @property
        def text(self) -> str:
            raise RuntimeError("stream consumed")

    assert _safe_text(Unreadable()) == "<unreadable>"
