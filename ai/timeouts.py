from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

from core.config import settings

T = TypeVar("T")


async def with_timeout(awaitable: Awaitable[T], timeout_sec: float, operation: str) -> T:
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_sec)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(f"{operation} timed out after {timeout_sec:g}s") from exc


CHAT_TIMEOUT_SEC = max(1.0, settings.ai_timeout_sec)
VISION_TIMEOUT_SEC = max(CHAT_TIMEOUT_SEC, 120.0)
GROUNDING_TIMEOUT_SEC = max(CHAT_TIMEOUT_SEC, 90.0)
