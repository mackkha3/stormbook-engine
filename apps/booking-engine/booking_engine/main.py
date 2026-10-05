"""FastAPI app + routes only. No business logic.

Phase 1 exposes just the health check; the GHL inbound webhook arrives in
Phase 2. Startup validates every client config and the templates, and
refuses to boot if anything is missing or still a placeholder.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request

from booking_engine import __version__
from booking_engine.config_loader import load_all_clients
from booking_engine.conversation import REQUIRED_TEMPLATES
from booking_engine.db import init_db, make_engine
from booking_engine.logger import configure as configure_logging
from booking_engine.logger import get
from booking_engine.settings import Settings
from booking_engine.store import Store
from booking_engine.templates import Templates

configure_logging()

log = get(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    app.state.started_at = time.monotonic()
    app.state.settings = settings
    app.state.templates = Templates.load()
    app.state.templates.require(REQUIRED_TEMPLATES)
    app.state.clients = load_all_clients(require_live=True)
    app.state.store = Store(init_db(make_engine(settings.database_url)))
    log.info(
        "startup",
        extra={
            "environment": settings.environment,
            "version": __version__,
            "clients": sorted(c.client_slug for c in app.state.clients.values()),
        },
    )
    yield
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
