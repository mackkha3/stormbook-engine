"""CORS middleware tests.

CORS is configured at app-creation time from CORS_ORIGINS, so each test
sets the env var, reloads `intake_api.main`, and constructs a TestClient
against the freshly-built app.
"""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def _make_app(monkeypatch, origins: str):
    monkeypatch.setenv("CORS_ORIGINS", origins)
    from intake_api import main as main_module

    importlib.reload(main_module)
    return main_module.app


def test_preflight_from_allowed_origin_returns_cors_headers(monkeypatch):
    app = _make_app(monkeypatch, "https://intake.stormbook.ai")
    client = TestClient(app)
    resp = client.options(
        "/intake",
        headers={
            "Origin": "https://intake.stormbook.ai",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "https://intake.stormbook.ai"
    assert "POST" in resp.headers["access-control-allow-methods"]


def test_preflight_from_disallowed_origin_omits_cors_headers(monkeypatch):
    app = _make_app(monkeypatch, "https://intake.stormbook.ai")
    client = TestClient(app)
    resp = client.options(
        "/intake",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in resp.headers


def test_default_origins_used_when_env_var_unset(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    from intake_api import main as main_module

    importlib.reload(main_module)
    client = TestClient(main_module.app)
    resp = client.options(
        "/intake",
        headers={
            "Origin": "http://localhost:4321",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:4321"


def test_multiple_origins_parsed(monkeypatch):
    app = _make_app(
        monkeypatch,
        "https://intake.stormbook.ai,https://stormbook.ai",
    )
    client = TestClient(app)
    r1 = client.options(
        "/intake",
        headers={
            "Origin": "https://intake.stormbook.ai",
            "Access-Control-Request-Method": "POST",
        },
    )
    r2 = client.options(
        "/intake",
        headers={
            "Origin": "https://stormbook.ai",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r1.headers["access-control-allow-origin"] == "https://intake.stormbook.ai"
    assert r2.headers["access-control-allow-origin"] == "https://stormbook.ai"
