"""FastAPI app entry point for the phone-in-my-hand demo relay.

Mirrors apps/intake-api's shape (lifespan-owned httpx client, request_id
middleware, JSON error bodies) without importing from it — the two services
have different lifecycles and this one gets deleted when Voice AI ships.

Lifespan additionally owns the in-memory ConversationStore and its 5-minute
expiry sweeper.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from demo_relay import __version__
from demo_relay.handlers import fire, health, twilio_inbound
from demo_relay.lib.conversation_store import ConversationStore, sweeper_loop
from demo_relay.lib.logger import configure as configure_logging
from demo_relay.lib.logger import get

load_dotenv()
configure_logging()

log = get(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.started_at = time.monotonic()
    app.state.http = httpx.AsyncClient(timeout=10.0)
    app.state.store = ConversationStore()
    app.state.sweeper = asyncio.create_task(sweeper_loop(app.state.store), name="sweeper")
    log.info(
        "startup",
        extra={
            "environment": os.environ.get("ENVIRONMENT", "development"),
            "version": __version__,
        },
    )
    try:
        yield
    finally:
        await app.state.store.cancel_follow_ups()
        app.state.sweeper.cancel()
        try:
            await app.state.sweeper
        except asyncio.CancelledError:
            pass
        await app.state.http.aclose()
        log.info("shutdown")


app = FastAPI(
    title="Stormbook Demo Relay",
    version=__version__,
    lifespan=lifespan,
)


_default_cors_origins = "http://localhost:4323,http://127.0.0.1:4323"
cors_origins = [
    o.strip()
    for o in os.environ.get("CORS_ORIGINS", _default_cors_origins).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Request-ID"],
    max_age=3600,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "")
    # Pydantic can park exception objects in `ctx`; round-trip through str
    # so JSONResponse can't blow up inside the error path itself.
    errors = json.loads(json.dumps(exc.errors(), default=str))
    log.warning(
        "validation_failed",
        extra={"request_id": request_id, "errors": errors},
    )
    return JSONResponse(
        {
            "code": "VALIDATION_ERROR",
            "request_id": request_id,
            "detail": {"errors": errors},
        },
        status_code=422,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "")
    log.error(
        "internal_error",
        extra={
            "request_id": request_id,
            "error": str(exc),
            "error_type": type(exc).__name__,
        },
        exc_info=exc,
    )
    return JSONResponse(
        {"code": "INTERNAL_ERROR", "request_id": request_id, "detail": None},
        status_code=500,
    )


app.include_router(health.router)
app.include_router(fire.router)
app.include_router(twilio_inbound.router)
