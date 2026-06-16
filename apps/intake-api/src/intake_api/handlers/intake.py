"""POST /intake — the intake form's submission target."""

from __future__ import annotations

from fastapi import APIRouter, Request

from intake_api.models import IntakePayload, IntakeResponse
from intake_api.process_lead import process_lead

router = APIRouter()


@router.post("/intake", response_model=IntakeResponse)
async def post_intake(payload: IntakePayload, request: Request) -> IntakeResponse:
    request_id: str = request.state.request_id
    http = request.app.state.http
    result = await process_lead(payload, request_id=request_id, http=http)
    return IntakeResponse(
        lead_id=result.lead_id,
        ghl_contact_id=result.ghl_contact_id,
        request_id=request_id,
    )
