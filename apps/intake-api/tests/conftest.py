"""Test fixtures.

* `set_test_env` (autouse) — populates required env vars so client_config
  + Meta handler tests resolve without needing a `.env` file.
* `app` — fresh FastAPI app per test (so lifespan + state are isolated).
* `client` — httpx AsyncClient bound to the ASGI transport.
* `mock_http` — pytest-httpx fixture is auto-discovered; we just rely on
  it patching httpx.AsyncClient.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Make required env vars available before the app imports modules that read them.
os.environ.setdefault("GHL_API_KEY_HENDERSON_ROOFING", "test_ghl_key_henderson")
os.environ.setdefault("META_VERIFY_TOKEN", "test_verify_token_abcdef0123456789")
os.environ.setdefault("META_APP_SECRET", "test_meta_app_secret")
os.environ.setdefault("META_PAGE_ACCESS_TOKEN", "test_page_token")
os.environ.setdefault("ENVIRONMENT", "development")
# Geocoding stays off in tests — leave GOOGLE_MAPS_API_KEY unset so the
# pipeline takes its "skip" branch instead of issuing real outbound calls.


@pytest.fixture(autouse=True)
def reset_client_config_cache():
    from intake_api.lib import client_config

    client_config.reset_cache_for_tests()
    yield
    client_config.reset_cache_for_tests()


@pytest.fixture
def app():
    from intake_api.main import app as _app

    return _app


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Lifespan startup runs lazily on first request via ASGITransport.
        async with app.router.lifespan_context(app):
            yield ac
