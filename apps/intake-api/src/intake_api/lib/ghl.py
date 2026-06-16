"""GoHighLevel API client.

Endpoint: https://services.leadconnectorhq.com/contacts/
Auth:     `Authorization: Bearer <client.ghl_api_key>`
Version:  `Version: 2021-07-28` (required by GHL)

One `httpx.AsyncClient` per process, reused across requests via main.py
lifespan. Retries on 5xx with exponential backoff (lib/retry.py).
"""

from __future__ import annotations

from typing import Any

import httpx

from intake_api.lib.client_config import ClientConfig
from intake_api.lib.errors import ApiException
from intake_api.lib.geocode import GeocodeResult
from intake_api.lib.logger import get
from intake_api.lib.retry import DEFAULT_DELAYS_MS, RetryError, retry_async
from intake_api.models import IntakePayload

log = get(__name__)

GHL_BASE_URL = "https://services.leadconnectorhq.com"
GHL_CONTACTS_PATH = "/contacts/"
GHL_VERSION_HEADER = "2021-07-28"
GHL_TIMEOUT_SECONDS = 10.0


def build_contact_payload(
    payload: IntakePayload,
    *,
    client: ClientConfig,
    stormbook_lead_id: str,
    geocode_result: GeocodeResult | None,
) -> dict[str, Any]:
    """Map a normalized intake payload + client config → GHL Contacts body.

    Field mapping is locked in `prompts/3-intake-api.md` (§ GHL API integration).
    """
    cf_ids = client.custom_field_ids
    custom_fields: list[dict[str, str]] = []

    def add_cf(key: str, value: str | None) -> None:
        cf_id = cf_ids.get(key)
        if cf_id and value is not None:
            custom_fields.append({"id": cf_id, "value": value})

    add_cf("damage_type", payload.damage_type)
    add_cf("stormbook_lead_id", stormbook_lead_id)
    add_cf("property_address", payload.address)

    tags = [f"source-{payload.source}", "roofing-storm"]

    body: dict[str, Any] = {
        "locationId": client.ghl_location_id,
        "firstName": payload.first_name,
        "lastName": payload.last_name,
        "phone": payload.phone,
        "address1": payload.address,
        "tags": tags,
        "customFields": custom_fields,
        "source": payload.source,
    }
    if payload.email:
        body["email"] = payload.email
    if geocode_result is not None:
        if geocode_result.state:
            body["state"] = geocode_result.state
        if geocode_result.zip_code:
            body["postalCode"] = geocode_result.zip_code

    return body


async def create_contact(
    payload: IntakePayload,
    *,
    client_cfg: ClientConfig,
    stormbook_lead_id: str,
    request_id: str,
    http: httpx.AsyncClient,
    geocode_result: GeocodeResult | None = None,
) -> str:
    """POST to GHL, retry on 5xx, return the new contact ID.

    Raises ApiException(GHL_UNAVAILABLE, 502) after retries exhausted —
    main.py converts that to the JSON error response.
    """
    body = build_contact_payload(
        payload,
        client=client_cfg,
        stormbook_lead_id=stormbook_lead_id,
        geocode_result=geocode_result,
    )
    headers = {
        "Authorization": f"Bearer {client_cfg.ghl_api_key}",
        "Version": GHL_VERSION_HEADER,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    url = GHL_BASE_URL + GHL_CONTACTS_PATH

    async def attempt(i: int) -> httpx.Response:
        log.debug(
            "ghl_post_attempt",
            extra={
                "attempt": i + 1,
                "request_id": request_id,
                "stormbook_lead_id": stormbook_lead_id,
                "client_id": client_cfg.client_id,
            },
        )
        return await http.post(url, json=body, headers=headers, timeout=GHL_TIMEOUT_SECONDS)

    def should_retry(result: httpx.Response | BaseException) -> bool:
        if isinstance(result, BaseException):
            return isinstance(result, (httpx.TransportError, httpx.TimeoutException))
        return result.status_code >= 500

    try:
        response = await retry_async(
            attempt, should_retry=should_retry, delays_ms=DEFAULT_DELAYS_MS
        )
    except RetryError as exc:
        log.error(
            "ghl_unrecoverable",
            extra={
                "request_id": request_id,
                "stormbook_lead_id": stormbook_lead_id,
                "client_id": client_cfg.client_id,
                "payload": payload.model_dump(mode="json"),
                "error": str(exc.last_exc),
            },
        )
        raise ApiException(
            "GHL_UNAVAILABLE",
            status_code=502,
            detail={"stormbook_lead_id": stormbook_lead_id, "reason": "network"},
        ) from exc

    if response.status_code >= 500:
        log.error(
            "ghl_unrecoverable",
            extra={
                "request_id": request_id,
                "stormbook_lead_id": stormbook_lead_id,
                "client_id": client_cfg.client_id,
                "status": response.status_code,
                "body": _safe_text(response),
                "payload": payload.model_dump(mode="json"),
            },
        )
        raise ApiException(
            "GHL_UNAVAILABLE",
            status_code=502,
            detail={"stormbook_lead_id": stormbook_lead_id, "ghl_status": response.status_code},
        )

    if response.status_code >= 400:
        # 4xx from GHL = our payload was wrong. Don't retry, but don't lose the lead either.
        log.error(
            "ghl_rejected",
            extra={
                "request_id": request_id,
                "stormbook_lead_id": stormbook_lead_id,
                "client_id": client_cfg.client_id,
                "status": response.status_code,
                "body": _safe_text(response),
            },
        )
        raise ApiException(
            "GHL_UNAVAILABLE",
            status_code=502,
            detail={
                "stormbook_lead_id": stormbook_lead_id,
                "ghl_status": response.status_code,
                "ghl_body": _safe_text(response),
            },
        )

    contact_id = _extract_contact_id(response)
    log.info(
        "ghl_post_success",
        extra={
            "request_id": request_id,
            "stormbook_lead_id": stormbook_lead_id,
            "client_id": client_cfg.client_id,
            "ghl_contact_id": contact_id,
        },
    )
    return contact_id


def _safe_text(response: httpx.Response) -> str:
    try:
        return response.text[:2000]
    except Exception:  # noqa: BLE001
        return "<unreadable>"


def _extract_contact_id(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    # GHL wraps the contact under `contact` for create; tolerate either shape.
    contact = body.get("contact") if "contact" in body else body
    if isinstance(contact, dict):
        return str(contact.get("id") or contact.get("contactId") or "")
    return ""
