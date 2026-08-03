"""GET /health — Railway liveness check."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from fastapi import APIRouter, Request

from demo_relay import __version__
from demo_relay.models import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    started_at: float = request.app.state.started_at
    return HealthResponse(
        status="ok",
        version=__version__,
        uptime_seconds=int(time.monotonic() - started_at),
        active_conversations_count=await request.app.state.store.count(),
        timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
