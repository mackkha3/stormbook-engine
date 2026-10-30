"""The Meta lead path: batch loop, Graph API fetch, field mapping, signature edges.

Every outbound call is intercepted by pytest-httpx.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
from datetime import UTC, datetime

import pytest

from intake_api.handlers.meta import _map_meta_lead_to_intake, _verify_signature

GHL_URL = "https://services.leadconnectorhq.com/contacts/"
CLIENT_ID = "henderson-roofing"


def _graph_url(leadgen_id: str) -> re.Pattern[str]:
    return re.compile(rf"https://graph\.facebook\.com/v19\.0/{leadgen_id}\?.*")


def _lead(**fields: str) -> dict:
    base = {
        "client_id": CLIENT_ID,
        "full_name": "Pat Homeowner",
        "phone_number": "+19035550199",
        "street_address": "123 Elm St, Tyler, TX 75701",
        "damage_type": "hail",
        **fields,
    }
    return {
        "created_time": "2026-06-13T15:00:00+0000",
        "field_data": [{"name": k, "values": [v]} for k, v in base.items()],
    }


def _webhook(*leadgen_ids: str) -> dict:
    return {
        "entry": [
            {"changes": [{"value": {"leadgen_id": lid, "page_id": "page-1", "form_id": "form-1"}}]}
            for lid in leadgen_ids
        ]
    }


async def _post(client, payload: dict):
    body = json.dumps(payload).encode()
    signature = hmac.new(os.environ["META_APP_SECRET"].encode(), body, hashlib.sha256).hexdigest()
    return await client.post(
        "/webhook/meta",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={signature}"},
    )


def _map(lead_data: dict, *, page_id: str | None = "page-1"):
    return _map_meta_lead_to_intake(lead_data, page_id=page_id)


# --- Signature edges ---


@pytest.mark.asyncio
async def test_missing_signature_header_is_rejected(client):
    resp = await client.post("/webhook/meta", content=b"{}")
    assert resp.status_code == 401
    assert resp.json()["code"] == "META_SIGNATURE_INVALID"


@pytest.mark.asyncio
async def test_signature_without_sha256_prefix_is_rejected(client):
    body = b"{}"
    digest = hmac.new(os.environ["META_APP_SECRET"].encode(), body, hashlib.sha256).hexdigest()

    resp = await client.post("/webhook/meta", content=body, headers={"X-Hub-Signature-256": digest})

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unset_app_secret_rejects_everything(client, monkeypatch):
    monkeypatch.delenv("META_APP_SECRET")
    resp = await client.post(
        "/webhook/meta", content=b"{}", headers={"X-Hub-Signature-256": "sha256=abc"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_signed_invalid_json_is_a_400(client):
    body = b"not json"
    digest = hmac.new(os.environ["META_APP_SECRET"].encode(), body, hashlib.sha256).hexdigest()

    resp = await client.post(
        "/webhook/meta", content=body, headers={"X-Hub-Signature-256": f"sha256={digest}"}
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == {"reason": "invalid json"}


# --- Batch loop + Graph fetch ---


@pytest.mark.asyncio
async def test_each_lead_in_a_batch_reaches_ghl(client, httpx_mock):
    for lid, name in (("lead-1", "Pat Homeowner"), ("lead-2", "Sam Roofowner")):
        httpx_mock.add_response(url=_graph_url(lid), json=_lead(full_name=name))
        httpx_mock.add_response(
            url=GHL_URL, method="POST", status_code=201, json={"contact": {"id": lid}}
        )

    resp = await _post(client, _webhook("lead-1", "lead-2"))

    assert resp.status_code == 200
    assert resp.json()["processed"] == 2
    assert resp.json()["failed"] == 0
    sent = [json.loads(r.content) for r in httpx_mock.get_requests(url=GHL_URL)]
    assert [b["firstName"] for b in sent] == ["Pat", "Sam"]
    assert all(b["source"] == "meta" for b in sent)


@pytest.mark.asyncio
async def test_graph_fetch_sends_the_page_token_and_fields(client, httpx_mock):
    httpx_mock.add_response(url=_graph_url("lead-1"), json=_lead())
    httpx_mock.add_response(
        url=GHL_URL, method="POST", status_code=201, json={"contact": {"id": "c1"}}
    )

    await _post(client, _webhook("lead-1"))

    params = httpx_mock.get_request(url=_graph_url("lead-1")).url.params
    assert params["access_token"] == os.environ["META_PAGE_ACCESS_TOKEN"]
    assert params["fields"] == "field_data,created_time,form_id"


@pytest.mark.asyncio
async def test_changes_without_a_leadgen_id_are_skipped(client, httpx_mock):
    payload = {
        "entry": [
            {"changes": None},
            {"changes": [{"value": {"page_id": "page-1"}}, {"value": None}, None]},
        ]
    }

    resp = await _post(client, payload)

    assert resp.status_code == 200
    assert resp.json()["processed"] == 0
    assert resp.json()["failed"] == 0
    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_graph_failure_is_counted_and_does_not_stop_the_batch(client, httpx_mock, caplog):
    httpx_mock.add_response(url=_graph_url("lead-bad"), status_code=500)
    httpx_mock.add_response(url=_graph_url("lead-good"), json=_lead())
    httpx_mock.add_response(
        url=GHL_URL, method="POST", status_code=201, json={"contact": {"id": "c1"}}
    )

    resp = await _post(client, _webhook("lead-bad", "lead-good"))

    assert resp.status_code == 200
    assert resp.json()["processed"] == 1
    assert resp.json()["failed"] == 1
    failure = next(r for r in caplog.records if r.getMessage() == "meta_lead_processing_failed")
    assert failure.leadgen_id == "lead-bad"


@pytest.mark.asyncio
async def test_unset_page_token_counts_the_lead_as_failed(client, httpx_mock, monkeypatch):
    monkeypatch.delenv("META_PAGE_ACCESS_TOKEN")

    resp = await _post(client, _webhook("lead-1"))

    assert resp.status_code == 200
    assert resp.json()["failed"] == 1
    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_unmappable_lead_is_counted_as_failed(client, httpx_mock):
    # IntakePayload needs an address; nothing on this form supplies one.
    httpx_mock.add_response(url=_graph_url("lead-1"), json=_lead(street_address=""))

    resp = await _post(client, _webhook("lead-1"))

    assert resp.status_code == 200
    assert resp.json()["failed"] == 1
    assert httpx_mock.get_requests(url=GHL_URL) == []


@pytest.mark.asyncio
async def test_ghl_rejection_propagates_so_meta_retries(client, httpx_mock):
    httpx_mock.add_response(url=_graph_url("lead-1"), json=_lead())
    httpx_mock.add_response(url=GHL_URL, method="POST", status_code=422, json={"message": "bad"})

    resp = await _post(client, _webhook("lead-1"))

    assert resp.status_code == 502
    assert resp.json()["code"] == "GHL_UNAVAILABLE"


# --- Field mapping ---


def test_full_name_splits_on_the_first_space():
    payload = _map(_lead(full_name="Mary Jo Smith"))
    assert (payload.first_name, payload.last_name) == ("Mary", "Jo Smith")


def test_explicit_names_win_over_full_name():
    payload = _map(_lead(full_name="Pat Homeowner", first_name="Patricia", last_name="Jones"))
    assert (payload.first_name, payload.last_name) == ("Patricia", "Jones")


def test_full_name_only_fills_the_missing_half():
    payload = _map(_lead(full_name="Pat Homeowner", first_name="Patricia"))
    assert (payload.first_name, payload.last_name) == ("Patricia", "Homeowner")


def test_single_word_name_defaults_the_last_name():
    payload = _map(_lead(full_name="Pat"))
    assert (payload.first_name, payload.last_name) == ("Pat", "Unknown")


def test_no_name_at_all_defaults_both():
    payload = _map(_lead(full_name=""))
    assert (payload.first_name, payload.last_name) == ("Lead", "Unknown")


def test_field_names_are_case_and_whitespace_insensitive():
    lead = {"field_data": [{"name": "  Phone_Number ", "values": ["+19035550199"]}]}
    lead["field_data"].append({"name": "city", "values": ["Tyler"]})

    payload = _map(lead)

    assert payload.phone == "+19035550199"
    assert payload.address == "Tyler"


def test_nameless_and_valueless_fields_are_ignored():
    lead = _lead()
    lead["field_data"] += [{"name": "", "values": ["x"]}, {"name": "email", "values": []}]

    assert _map(lead).email is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("hail", "Hail"), ("WIND", "Wind"), ("Water", "Water"), ("not sure", "Not sure"),
     ("tornado", "Not sure"), ("", "Not sure")],
)
def test_damage_type_is_normalized(raw, expected):
    assert _map(_lead(damage_type=raw)).damage_type == expected


def test_client_id_field_wins_over_page_id():
    assert _map(_lead(client_id="from-form")).client_id == "from-form"


def test_client_id_falls_back_to_page_id():
    assert _map(_lead(client_id=""), page_id="page-9").client_id == "page-9"


def test_created_time_becomes_submitted_at():
    assert _map(_lead()).submitted_at == datetime(2026, 6, 13, 15, 0, tzinfo=UTC)


@pytest.mark.parametrize("created_time", [None, "not-a-date"])
def test_unusable_created_time_falls_back_to_now(created_time):
    lead = {**_lead(), "created_time": created_time}
    before = datetime.now(UTC)

    assert _map(lead).submitted_at >= before


@pytest.mark.parametrize(
    ("answer", "consented"),
    [("Yes", True), ("true", True), ("1", True), ("no", False), ("", False)],
)
def test_only_an_explicit_answer_counts_as_sms_consent(answer, consented):
    payload = _map(_lead(sms_consent=answer))
    assert payload.sms_consent is consented
    assert payload.sms_consent_at == payload.submitted_at


# --- Secrets in logs ---


@pytest.mark.asyncio
@pytest.mark.parametrize("graph_status", [200, 400, 500])
async def test_page_token_never_reaches_the_logs(client, httpx_mock, caplog, graph_status):
    caplog.set_level(logging.DEBUG)
    lead = _lead() if graph_status == 200 else {"error": "nope"}
    httpx_mock.add_response(url=_graph_url("lead-1"), status_code=graph_status, json=lead)
    if graph_status == 200:
        httpx_mock.add_response(
            url=GHL_URL, method="POST", status_code=201, json={"contact": {"id": "c1"}}
        )

    await _post(client, _webhook("lead-1"))

    # Formats every record's fields too, the same way the JSON logger emits them.
    output = "\n".join(f"{r.getMessage()} {vars(r)}" for r in caplog.records)
    assert "access_token=" not in output
    assert os.environ["META_PAGE_ACCESS_TOKEN"] not in output


@pytest.mark.asyncio
async def test_graph_error_log_keeps_the_type_and_status(client, httpx_mock, caplog):
    httpx_mock.add_response(url=_graph_url("lead-1"), status_code=500)

    await _post(client, _webhook("lead-1"))

    failure = next(r for r in caplog.records if r.getMessage() == "meta_lead_processing_failed")
    assert failure.error == "HTTPStatusError: Graph API returned 500"


@pytest.mark.asyncio
async def test_validation_error_log_names_the_field_not_the_value(client, httpx_mock, caplog):
    httpx_mock.add_response(url=_graph_url("lead-1"), json=_lead(street_address=""))

    await _post(client, _webhook("lead-1"))

    failure = next(r for r in caplog.records if r.getMessage() == "meta_lead_processing_failed")
    assert failure.error == "ValidationError: invalid fields: address"
    assert "+19035550199" not in failure.error


def test_non_ascii_signature_is_rejected():
    assert _verify_signature("secret", b"{}", "sha256=sïgnature") is False


@pytest.mark.asyncio
async def test_non_ascii_signature_is_a_401_not_a_500(client):
    resp = await client.post(
        "/webhook/meta",
        content=b"{}",
        headers={"X-Hub-Signature-256": "sha256=sïg".encode("latin-1")},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_non_ascii_verify_token_is_a_403_not_a_500(client):
    resp = await client.get(
        "/webhook/meta",
        params={"hub.mode": "subscribe", "hub.verify_token": "tökén", "hub.challenge": "c"},
    )
    assert resp.status_code == 403
