# TODO: implement when first Angi-using client onboards; payload format is TBD.
"""POST /webhook/angi — stub. Logs and 200s. Real parsing waits until we
have a sample Angi webhook payload from a live client."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from intake_api.lib.logger import get

router = APIRouter()
log = get(__name__)


@router.post("/webhook/angi")
async def angi_webhook(request: Request) -> JSONResponse:
    try:
        body: Any = await request.json()
    except ValueError:
        body = None
    log.info(
        "angi_webhook_received_stub",
        extra={
            "request_id": request.state.request_id,
            "body_type": type(body).__name__,
            "body_keys": list(body.keys()) if isinstance(body, dict) else None,
        },
    )
    return JSONResponse({"status": "stub", "request_id": request.state.request_id})
