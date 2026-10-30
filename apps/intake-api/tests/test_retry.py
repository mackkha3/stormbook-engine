from __future__ import annotations

import pytest

from intake_api.lib import retry
from intake_api.lib.retry import RetryError, retry_async

DELAYS = (200, 800, 3200)


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(retry.asyncio, "sleep", fake_sleep)
    return recorded


def _scripted(*outcomes):
    """fn(attempt) that raises or returns the next outcome, recording calls."""
    calls: list[int] = []

    async def fn(attempt: int):
        calls.append(attempt)
        outcome = outcomes[attempt]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return fn, calls


def _retry_exceptions_and_bad(result) -> bool:
    return isinstance(result, ConnectionError) or result == "bad"


@pytest.mark.asyncio
async def test_transient_exception_retries_until_success(sleeps):
    fn, calls = _scripted(ConnectionError("a"), ConnectionError("b"), "ok")

    result = await retry_async(fn, should_retry=_retry_exceptions_and_bad, delays_ms=DELAYS)

    assert result == "ok"
    assert calls == [0, 1, 2]
    assert sleeps == [0.2, 0.8]


@pytest.mark.asyncio
async def test_exhausted_exceptions_raise_retry_error(sleeps):
    last = ConnectionError("final")
    fn, calls = _scripted(ConnectionError("a"), ConnectionError("b"), ConnectionError("c"), last)

    with pytest.raises(RetryError) as excinfo:
        await retry_async(fn, should_retry=_retry_exceptions_and_bad, delays_ms=DELAYS)

    assert excinfo.value.last_exc is last
    assert excinfo.value.attempts == 4
    assert excinfo.value.__cause__ is last
    assert "all 4 retry attempts failed" in str(excinfo.value)
    assert len(calls) == 4
    # No sleep after the final attempt.
    assert sleeps == [0.2, 0.8, 3.2]


@pytest.mark.asyncio
async def test_non_retryable_exception_raises_immediately(sleeps):
    boom = ValueError("not transient")
    fn, calls = _scripted(boom)

    with pytest.raises(ValueError) as excinfo:
        await retry_async(fn, should_retry=_retry_exceptions_and_bad, delays_ms=DELAYS)

    assert excinfo.value is boom
    assert calls == [0]
    assert sleeps == []


@pytest.mark.asyncio
async def test_final_retry_worthy_result_is_returned_not_raised(sleeps):
    fn, calls = _scripted("bad", "bad", "bad", "bad")

    result = await retry_async(fn, should_retry=_retry_exceptions_and_bad, delays_ms=DELAYS)

    assert result == "bad"
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_exception_then_bad_result_surfaces_the_result(sleeps):
    fn, _ = _scripted(ConnectionError("a"), "bad")

    result = await retry_async(fn, should_retry=_retry_exceptions_and_bad, delays_ms=(1,))

    assert result == "bad"


@pytest.mark.asyncio
async def test_bad_result_then_exception_raises_retry_error(sleeps):
    last = ConnectionError("final")
    fn, _ = _scripted("bad", last)

    with pytest.raises(RetryError) as excinfo:
        await retry_async(fn, should_retry=_retry_exceptions_and_bad, delays_ms=(1,))

    assert excinfo.value.last_exc is last
