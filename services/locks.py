from __future__ import annotations

import asyncio
import weakref

_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_guard = asyncio.Lock()


async def user_lock(user_id: str) -> asyncio.Lock:
    async with _guard:
        lock = _locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _locks[user_id] = lock
        return lock
