"""Meta Lead Ads webhook.

GET — Meta's verification challenge. Return `hub.challenge` as plain text
if `hub.mode == "subscribe"` and `hub.verify_token` matches `META_VERIFY_TOKEN`.

POST — Verify `X-Hub-Signature-256` via HMAC-SHA256 using `META_APP_SECRET`
(timing-safe compare). Parse `entry[].changes[].value.leadgen_id`, fetch
lead details from Meta's Graph API with `META_PAGE_ACCESS_TOKEN`, map to
IntakePayload, then call the shared pipeline.

Meta expects a 2xx within 5s or it retries — we keep the handler tight.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from intake_api.lib.errors import ApiException
from intake_api.lib.logger import get
from intake_api.models import IntakePayload
from intake_api.process_lead import process_lead

router = APIRouter()
log = get(__name__)

META_GRAPH_URL = "https://graph.facebook.com/v19.0"


@router.get("/webhook/meta")
async def meta_verify(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
) -> PlainTextResponse:
    expected = os.environ.get("META_VERIFY_TOKEN", "")
    if hub_mode == "subscribe" and expected and hmac.compare_digest(hub_verify_token, expected):
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=403, detail="verification failed")


@router.post("/webhook/meta")
async def meta_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> JSONResponse:
    raw_body = await request.body()
    secret = os.environ.get("META_APP_SECRET", "")
    if not secret or not _verify_signature(secret, raw_body, x_hub_signature_256):
        log.warning(
            "meta_signature_invalid",
            extra={"request_id": request.state.request_id, "header_present": bool(x_hub_signature_256)},
        )
        raise ApiException("META_SIGNATURE_INVALID", status_code=401)

    try:
        body: dict[str, Any] = await request.json()
    except ValueError as exc:
        raise ApiException(
            "VALIDATION_ERROR",
            status_code=400,
            detail={"reason": "invalid json"},
        ) from exc

    http = request.app.state.http
    request_id: str = request.state.request_id
    processed = 0
    failed = 0

    for entry in body.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = (change or {}).get("value") or {}
            leadgen_id = value.get("leadgen_id")
            page_id = value.get("page_id")
            form_id = value.get("form_id")
            if not leadgen_id:
                continue
            try:
                lead_data = await _fetch_meta_lead(http, leadgen_id)
                log.info(
                    "meta_lead_fetched",
                    extra={
                        "request_id": request_id,
                        "leadgen_id": leadgen_id,
                        "page_id": page_id,
                        "form_id": form_id,
                    },
                )
                payload = _map_meta_lead_to_intake(lead_data, page_id=page_id)
                await process_lead(payload, request_id=request_id, http=http)
                processed += 1
            except ApiException:
                # Re-raise client/config/GHL errors — Meta will retry the whole webhook.
                raise
            except Exception as exc:  # noqa: BLE001
                failed += 1
                log.error(
                    "meta_lead_processing_failed",
                    extra={
                        "request_id": request_id,
                        "leadgen_id": leadgen_id,
                        "error": str(exc),
                    },
                )

    return JSONResponse(
        {"request_id": request_id, "processed": processed, "failed": failed},
        status_code=200,
    )


def _verify_signature(secret: str, body: bytes, header_value: str | None) -> bool:
    if not header_value:
        return False
    if not header_value.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    provided = header_value[len("sha256=") :]
    return hmac.compare_digest(expected, provided)


async def _fetch_meta_lead(http: Any, leadgen_id: str) -> dict[str, Any]:
    access_token = os.environ.get("META_PAGE_ACCESS_TOKEN", "")
    if not access_token:
        raise RuntimeError("META_PAGE_ACCESS_TOKEN not set")
    url = f"{META_GRAPH_URL}/{leadgen_id}"
    params = {"access_token": access_token, "fields": "field_data,created_time,form_id"}
    resp = await http.get(url, params=params, timeout=8.0)
    resp.raise_for_status()
    return resp.json()


def _map_meta_lead_to_intake(
    lead_data: dict[str, Any], *, page_id: str | None
) -> IntakePayload:
    """Best-effort translation of Meta's `field_data[]` into our payload.

    Meta sends `field_data: [{name, values: [...]}, ...]`. Field names are
    operator-defined inside Meta Lead Form Studio, so we accept the common
    canonical names and let the form designer match them.
    """
    fields: dict[str, str] = {}
    for fd in lead_data.get("field_data", []) or []:
        name = (fd.get("name") or "").lower().strip()
        values = fd.get("values") or []
        if name and values:
            fields[name] = str(values[0])

    def first_of(*candidates: str, default: str = "") -> str:
        for k in candidates:
            if k in fields and fields[k]:
                return fields[k]
        return default

    full_name = first_of("full_name", "name")
    first_name = first_of("first_name", "firstname")
    last_name = first_of("last_name", "lastname")
    if (not first_name or not last_name) and full_name:
        parts = full_name.split(None, 1)
        first_name = first_name or parts[0]
        last_name = last_name or (parts[1] if len(parts) > 1 else "")

    client_id = first_of("client_id", "stormbook_client_id") or (page_id or "")
    damage = first_of("damage_type", "damage").strip().title()
    if damage not in ("Hail", "Wind", "Water", "Not sure"):
        damage = "Not sure"

    submitted_raw = lead_data.get("created_time")
    try:
        submitted_at = datetime.fromisoformat(submitted_raw.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        submitted_at = datetime.now(UTC)

    return IntakePayload(
        client_id=client_id,
        source="meta",
        first_name=first_name or "Lead",
        last_name=last_name or "Unknown",
        phone=first_of("phone_number", "phone"),
        email=first_of("email") or None,
        address=first_of("street_address", "address", "city"),
        damage_type=damage,  # type: ignore[arg-type]
        photo_count=0,
        submitted_at=submitted_at,
    )
