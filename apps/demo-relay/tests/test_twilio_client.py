from __future__ import annotations

import httpx
import pytest

from demo_relay.lib import twilio_client
from demo_relay.lib.twilio_client import (
    TwilioConfig,
    TwilioConfigError,
    TwilioSendError,
    _safe_text,
    send_sms,
)
from tests.conftest import TWILIO_MESSAGES_URL

ACCOUNT_SID = "ACtestsid0000000000000000000000000"


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(twilio_client.asyncio, "sleep", fake_sleep)
    return recorded


async def _send() -> str:
    async with httpx.AsyncClient() as http:
        return await send_sms(
            "+19035550199",
            "hello",
            from_number="+19035550100",
            account_sid=ACCOUNT_SID,
            auth_token="token",
            http=http,
        )


def test_from_env_reads_and_strips_credentials(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", " ACabc ")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+19035550100")

    assert TwilioConfig.from_env() == TwilioConfig("ACabc", "tok", "+19035550100")


def test_missing_credentials_name_every_absent_var(monkeypatch):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "   ")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+19035550100")

    with pytest.raises(TwilioConfigError) as excinfo:
        TwilioConfig.from_env()

    assert str(excinfo.value) == "missing env vars: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN"


@pytest.mark.asyncio
async def test_network_errors_exhaust_retries(httpx_mock, sleeps):
    for _ in range(4):
        httpx_mock.add_exception(httpx.ConnectError("refused"))

    with pytest.raises(TwilioSendError, match="ConnectError: refused"):
        await _send()

    assert len(httpx_mock.get_requests()) == 4
    assert sleeps == [0.2, 0.8, 3.2]


@pytest.mark.asyncio
async def test_5xx_exhausts_retries(httpx_mock, sleeps):
    for _ in range(4):
        httpx_mock.add_response(url=TWILIO_MESSAGES_URL, status_code=503, text="down")

    with pytest.raises(TwilioSendError, match="HTTP 503: down"):
        await _send()

    assert len(httpx_mock.get_requests()) == 4


@pytest.mark.asyncio
async def test_transient_failure_then_success_returns_the_sid(httpx_mock, sleeps):
    httpx_mock.add_exception(httpx.ReadTimeout("slow"))
    httpx_mock.add_response(url=TWILIO_MESSAGES_URL, status_code=500)
    httpx_mock.add_response(url=TWILIO_MESSAGES_URL, status_code=201, json={"sid": "SM1"})

    assert await _send() == "SM1"
    assert sleeps == [0.2, 0.8]


@pytest.mark.asyncio
async def test_4xx_is_not_retried(httpx_mock, sleeps):
    httpx_mock.add_response(url=TWILIO_MESSAGES_URL, status_code=400, json={"code": 21211})

    with pytest.raises(TwilioSendError, match="HTTP 400"):
        await _send()

    assert len(httpx_mock.get_requests()) == 1
    assert sleeps == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"text": "not json"}, {"json": ["unexpected"]}])
async def test_unparseable_success_body_returns_empty_sid(httpx_mock, kwargs):
    httpx_mock.add_response(url=TWILIO_MESSAGES_URL, status_code=201, **kwargs)

    assert await _send() == ""


def test_safe_text_survives_an_unreadable_body():
    class Unreadable:
        @property
        def text(self) -> str:
            raise RuntimeError("stream consumed")

    assert _safe_text(Unreadable()) == "<unreadable>"
