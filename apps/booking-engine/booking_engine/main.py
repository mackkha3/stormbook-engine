"""FastAPI app + routes only. No business logic.

Startup validates every client config, the templates and the server's env
vars, and refuses to boot if anything is missing or still a placeholder.
The inbound webhook's work is in webhook.py.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from booking_engine import __version__
from booking_engine.config_loader import ConfigError, load_all_clients
from booking_engine.conversation import REQUIRED_TEMPLATES
from booking_engine.db import init_db, make_engine
from booking_engine.ghl_client import GhlError
from booking_engine.inbound_payload import (
    SECRET_HEADER,
    SECRET_PARAM,
    BadPayload,
    parse_body,
    secret_ok,
)
from booking_engine.logger import configure as configure_logging
from booking_engine.logger import get
from booking_engine.settings import Settings
from booking_engine.store import Store
from booking_engine.templates import Templates
from booking_engine.understanding import AnthropicUnderstander
from booking_engine.webhook import build_runtimes, handle_inbound

configure_logging()

log = get(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    missing = settings.missing_for_server()
    if missing:
        raise ConfigError(f"these env vars must be set to run the server: {', '.join(missing)}")
    app.state.started_at = time.monotonic()
    app.state.settings = settings
    app.state.templates = Templates.load()
    app.state.templates.require(REQUIRED_TEMPLATES)
    app.state.clients = load_all_clients(require_live=True)
    app.state.store = Store(init_db(make_engine(settings.database_url)))
    async with (
        httpx.AsyncClient() as http,
        AnthropicUnderstander(settings.anthropic_api_key) as understander,
    ):
        app.state.runtimes = build_runtimes(
            settings=settings, clients=app.state.clients, templates=app.state.templates,
            store=app.state.store, understander=understander, http=http,
        )
        log.info(
            "startup",
            extra={
                "environment": settings.environment,
                "version": __version__,
                "clients": sorted(c.client_slug for c in app.state.clients.values()),
            },
        )
        yield
        # Finish the turns still inside their debounce window before the clients close.
        for runtime in app.state.runtimes.values():
            await runtime.engine.debouncer.drain()
    log.info("shutdown")


app = FastAPI(title="Stormbook Booking Engine", version=__version__, lifespan=lifespan)


@app.get("/health")
async def health(request: Request) -> dict:
    return {
        "status": "ok",
        "version": __version__,
        "uptime_seconds": int(time.monotonic() - request.app.state.started_at),
        "clients": len(request.app.state.clients),
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@app.post("/webhooks/ghl/inbound")
async def ghl_inbound(request: Request) -> JSONResponse:
    state = request.app.state
    provided = request.headers.get(SECRET_HEADER) or request.query_params.get(SECRET_PARAM)
    if not secret_ok(provided, state.settings.ghl_webhook_secret):
        log.warning("webhook_rejected", extra={"reason": "bad_secret"})
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        inbound = parse_body((await request.body()).decode("utf-8", errors="replace"))
    except BadPayload as exc:
        log.warning("webhook_rejected", extra={"reason": str(exc)})
        return JSONResponse({"error": "bad_payload", "detail": str(exc)}, status_code=422)
    try:
        return JSONResponse(await handle_inbound(state.runtimes, inbound))
    except GhlError as exc:
        log.error("webhook_failed", extra={"contact_id": inbound.contact_id, "error": str(exc)})
        return JSONResponse({"error": "ghl_unavailable"}, status_code=502)
