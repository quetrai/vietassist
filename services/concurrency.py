from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_ai_slots: asyncio.Semaphore | None = None


def _get_ai_slots(limit: int) -> asyncio.Semaphore:
    global _ai_slots
    if _ai_slots is None:
        _ai_slots = asyncio.Semaphore(limit)
    return _ai_slots


@asynccontextmanager
async def assistant_turn(limit: int = 3) -> AsyncIterator[None]:
    async with _get_ai_slots(limit):
        yield
