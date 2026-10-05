"""Debounce: collect a homeowner's burst of texts into one turn.

Each new message for a key restarts that key's timer. When the timer runs
out, everything buffered is handed to the handler as a single batch. Batches
for the same key never overlap: texts that arrive while a turn is being
processed wait and become the next turn.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from booking_engine.logger import get

log = get(__name__)

Handler = Callable[[str, list[Any]], Awaitable[Any]]


class Debouncer:
    def __init__(self, delay_seconds: float, handler: Handler) -> None:
        self._delay = delay_seconds
        self._handler = handler
        self._buffers: dict[str, list[Any]] = defaultdict(list)
        self._timers: dict[str, asyncio.Task] = {}     # tasks still waiting out the delay
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tasks: set[asyncio.Task] = set()

    def submit(self, key: str, item: Any) -> None:
        self._buffers[key].append(item)
        timer = self._timers.pop(key, None)
        if timer is not None:
            timer.cancel()
        task = asyncio.create_task(self._run(key), name=f"debounce:{key}")
        self._timers[key] = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, key: str) -> None:
        await asyncio.sleep(self._delay)
        # Past the delay: unregister before any await, so a later submit starts a
        # new timer instead of cancelling a turn that is already in flight.
        if self._timers.get(key) is asyncio.current_task():
            del self._timers[key]
        async with self._locks[key]:
            items = self._buffers.pop(key, [])
            if not items:
                return
            try:
                await self._handler(key, items)
            except Exception:
                log.exception("debounced_turn_failed", extra={"key": key})

    async def drain(self) -> None:
        """Wait until every buffered message has been processed."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
