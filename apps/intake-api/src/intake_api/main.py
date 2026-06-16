"""FastAPI app entry point.

* `lifespan` owns the shared httpx.AsyncClient and records start time.
* request_id middleware stamps a UUID v4 on every request — both
  `request.state.request_id` (for handlers) and the `X-Request-ID`
  response header (for log correlation downstream).
* Exception handlers convert ApiException + Pydantic ValidationError into
  ApiError JSON bodies; everything else becomes a generic INTERNAL_ERROR.
"""

from __future__ import annotations

import os
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from intake_api.handlers import angi, health, intake, meta
from intake_api.lib.errors import ApiError, ApiException
from intake_api.lib.logger import configure as configure_logging
from intake_api.lib.logger import get

load_dotenv()
configure_logging()

log = get(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.started_at = time.monotonic()
    app.state.http = httpx.AsyncClient(timeout=10.0)
    log.info("startup", extra={"environment": os.environ.get("ENVIRONMENT", "development")})
    try:
        yield
    finally:
        await app.state.http.aclose()
        log.info("shutdown")


app = FastAPI(
    title="Stormbook Intake API",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(ApiException)
async def api_exception_handler(request: Request, exc: ApiException) -> JSONResponse:
    err = ApiError(
        code=exc.code,
        request_id=getattr(request.state, "request_id", ""),
        detail=exc.detail,
    )
    return JSONResponse(err.model_dump(), status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    log.warning(
        "validation_failed",
        extra={
            "request_id": getattr(request.state, "request_id", ""),
            "errors": exc.errors(),
        },
    )
    err = ApiError(
        code="VALIDATION_ERROR",
        request_id=getattr(request.state, "request_id", ""),
        detail={"errors": exc.errors()},
    )
    return JSONResponse(err.model_dump(), status_code=422)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.error(
        "internal_error",
        extra={
            "request_id": getattr(request.state, "request_id", ""),
            "error": str(exc),
            "error_type": type(exc).__name__,
        },
        exc_info=True,
    )
    err = ApiError(
        code="INTERNAL_ERROR",
        request_id=getattr(request.state, "request_id", ""),
        detail=None,
    )
    return JSONResponse(err.model_dump(), status_code=500)


app.include_router(health.router)
app.include_router(intake.router)
app.include_router(meta.router)
app.include_router(angi.router)
