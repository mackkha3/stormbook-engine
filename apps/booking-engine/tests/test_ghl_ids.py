"""The ids helper (read-only GHL lookups) and the Postgres URL rewrite."""

from __future__ import annotations

import argparse

import httpx
import pytest

from booking_engine import ghl_ids
from booking_engine.db import make_engine, normalize_url
from tests.ghl_fake import CALENDAR, LOCATION, PIPELINE, STAGES, TOKEN, FakeGhl


@pytest.fixture
def fake(monkeypatch) -> FakeGhl:
    fake = FakeGhl()
    monkeypatch.setenv("GHL_TOKEN_ROOF_DEMO", TOKEN)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        ghl_ids.httpx, "AsyncClient",
        lambda: real_client(transport=httpx.MockTransport(fake._handle)),
    )
    return fake


def args(location: str = LOCATION) -> argparse.Namespace:
    return ghl_ids.build_parser().parse_args(["--location", location])


async def test_prints_calendars_and_pipelines_with_stages_and_never_the_token(fake, capsys):
    assert await ghl_ids.run(args()) == 0
    out = capsys.readouterr().out
    assert f"Roof inspections {' ' * 24}{CALENDAR}" in out
    assert "Storm leads" in out and PIPELINE in out
    for name, stage_id in (("Texted", STAGES.texted), ("Replied", STAGES.replied),
                           ("Booked", STAGES.booked), ("No response", STAGES.no_response)):
        assert name in out and stage_id in out
    assert TOKEN not in out


async def test_makes_only_read_only_calls_for_the_given_location(fake):
    await ghl_ids.run(args("loc-xyz"))
    assert [(r.method, r.url.path) for r in fake.requests] == [
        ("GET", "/calendars/"), ("GET", "/opportunities/pipelines"),
    ]
    assert all(r.url.params["locationId"] == "loc-xyz" for r in fake.requests)
    assert [r.headers["Version"] for r in fake.requests] == ["2021-04-15", "2021-07-28"]


async def test_a_rejected_token_explains_itself_without_printing_it(fake, capsys):
    fake.fail[("GET", "/calendars/")].append(httpx.Response(401, json={"message": "Invalid JWT"}))
    assert await ghl_ids.run(args()) == 1
    err = capsys.readouterr().err
    assert "rejected the token" in err and TOKEN not in err


async def test_a_missing_token_is_reported_before_any_call(fake, monkeypatch, capsys):
    monkeypatch.setenv("GHL_TOKEN_ROOF_DEMO", "")
    assert await ghl_ids.run(args()) == 1
    assert "GHL_TOKEN_ROOF_DEMO is not set" in capsys.readouterr().err and fake.requests == []


def test_location_is_required():
    with pytest.raises(SystemExit):
        ghl_ids.build_parser().parse_args([])


@pytest.mark.parametrize("url", [
    "postgres://u:p@host.railway.internal:5432/railway",
    "postgresql://u:p@host.railway.internal:5432/railway",
    "postgresql+psycopg://u:p@host.railway.internal:5432/railway",
])
def test_railway_postgres_urls_are_pointed_at_the_installed_driver(url):
    assert normalize_url(url) == "postgresql+psycopg://u:p@host.railway.internal:5432/railway"
    engine = make_engine(url)                     # builds without connecting; driver imports
    assert engine.dialect.driver == "psycopg" and engine.url.password == "p"


def test_sqlite_urls_are_left_alone():
    assert normalize_url("sqlite:///./booking_engine.db") == "sqlite:///./booking_engine.db"
