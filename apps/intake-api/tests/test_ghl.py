from __future__ import annotations

from datetime import UTC, datetime

from intake_api.lib.client_config import ClientConfig
from intake_api.lib.geocode import GeocodeResult
from intake_api.lib.ghl import build_contact_payload
from intake_api.models import IntakePayload


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
