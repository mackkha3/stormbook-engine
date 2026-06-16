"""Shared pipeline: validate → normalize → geocode → GHL POST.

Called by /intake (form/web direct) and /webhook/meta (after Meta lead
fetch). The Angi webhook will share this path too once we know its
payload format.
"""

from __future__ import annotations

import uuid

import httpx

from intake_api.lib import client_config as client_config_lib
from intake_api.lib import geocode as geocode_lib
from intake_api.lib import ghl
from intake_api.lib.errors import ApiException
from intake_api.lib.logger import get
from intake_api.lib.normalize import normalize_address, normalize_phone
from intake_api.models import IntakePayload

log = get(__name__)


class ProcessResult:
    __slots__ = ("lead_id", "ghl_contact_id")

    def __init__(self, lead_id: str, ghl_contact_id: str) -> None:
        self.lead_id = lead_id
        self.ghl_contact_id = ghl_contact_id


async def process_lead(
    payload: IntakePayload,
    *,
    request_id: str,
    http: httpx.AsyncClient,
) -> ProcessResult:
    """Run the full pipeline. Raises ApiException for any failure mode."""
    stormbook_lead_id = str(uuid.uuid4())

    log.info(
        "intake_received",
        extra={
            "request_id": request_id,
            "stormbook_lead_id": stormbook_lead_id,
            "client_id": payload.client_id,
            "source": payload.source,
        },
    )

    # 1) Client config lookup (404 if unknown)
    client_cfg = client_config_lib.get_client(payload.client_id)
    if client_cfg is None:
        log.warning(
            "client_not_found",
            extra={"request_id": request_id, "client_id": payload.client_id},
        )
        raise ApiException(
            "CLIENT_NOT_FOUND",
            status_code=404,
            detail={"client_id": payload.client_id},
        )

    # 2) Defensive normalization
    normalized_phone = normalize_phone(payload.phone) or payload.phone
    normalized_address = normalize_address(payload.address) or payload.address
    payload = payload.model_copy(update={"phone": normalized_phone, "address": normalized_address})

    # 3) Geocode — non-blocking (warning + proceed on failure).
    geocode_result = await geocode_lib.geocode(payload.address, client=http)

    # 4) GHL POST with retry — raises ApiException(GHL_UNAVAILABLE) on exhaustion.
    contact_id = await ghl.create_contact(
        payload,
        client_cfg=client_cfg,
        stormbook_lead_id=stormbook_lead_id,
        request_id=request_id,
        http=http,
        geocode_result=geocode_result,
    )

    return ProcessResult(lead_id=stormbook_lead_id, ghl_contact_id=contact_id)
