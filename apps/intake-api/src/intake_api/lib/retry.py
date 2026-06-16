"""Exponential backoff helper for transient outbound failures.

Used for GHL POSTs: 3 attempts at 200ms → 800ms → 3200ms.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


DEFAULT_DELAYS_MS = (200, 800, 3200)


class RetryError(Exception):
    """Raised when all retry attempts failed. `last_exc` is the final exception."""

    def __init__(self, last_exc: BaseException | None, attempts: int) -> None:
        super().__init__(f"all {attempts} retry attempts failed")
        self.last_exc = last_exc
        self.attempts = attempts


async def retry_async(
    fn: Callable[[int], Awaitable[T]],
    *,
    should_retry: Callable[[BaseException | T], bool],
    delays_ms: tuple[int, ...] = DEFAULT_DELAYS_MS,
) -> T:
    """Run `fn(attempt)` up to `len(delays_ms) + 1` times.

    Sleeps `delays_ms[i]` BEFORE attempt i+1 (i.e. between retries, not before
    the first attempt). `should_retry` decides on either the raised exception
    or the returned value (e.g. an HTTP response with a 5xx status).

    Returns the last result. Raises RetryError if every attempt indicated
    retry (including the final one).
    """
    last_exc: BaseException | None = None
    last_result: T | None = None
    attempts = len(delays_ms) + 1

    for attempt in range(attempts):
        try:
            result = await fn(attempt)
        except BaseException as exc:  # noqa: BLE001 — caller's should_retry decides
            if not should_retry(exc):
                raise
            last_exc = exc
            last_result = None
        else:
            if not should_retry(result):
                return result
            last_result = result
            last_exc = None

        if attempt < attempts - 1:
            await asyncio.sleep(delays_ms[attempt] / 1000)

    if last_exc is not None:
        raise RetryError(last_exc, attempts) from last_exc
    # Final attempt returned a retry-worthy result — surface it rather than discard.
    return last_result  # type: ignore[return-value]
