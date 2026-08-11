from __future__ import annotations

import asyncio
import logging

import httpx

from core import database
from core.config import settings

logger = logging.getLogger(__name__)
_TIMEOUT = httpx.Timeout(30.0)
_client: httpx.AsyncClient | None = None


def _http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=_TIMEOUT)
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def send_message(external_id: str, text: str) -> bool:
    if not settings.zalo_enabled:
        return False
    url = f"http://127.0.0.1:{settings.zalo_control_port}/send"
    try:
        response = await _http_client().post(
            url,
            json={"to": external_id, "text": text},
            headers={"x-bridge-secret": settings.bridge_secret},
        )
        response.raise_for_status()
    except httpx.HTTPError:
        logger.warning("Zalo gateway send failed for %s", external_id)
        return False
    return True


async def enqueue_message(external_id: str, text: str) -> int:
    return await database.enqueue_zalo_message(external_id, text)


async def flush_outbox(limit: int = 20) -> int:
    sent = 0
    for row in await database.claim_zalo_outbox(limit):
        try:
            if not await send_message(str(row["recipient_id"]), str(row["content"])):
                raise RuntimeError("Zalo gateway unavailable")
            await database.mark_zalo_outbox_sent(int(row["id"]), str(row["lease_token"]))
            sent += 1
        except Exception as exc:
            await database.mark_zalo_outbox_failed(
                int(row["id"]), str(row["lease_token"]), str(exc)
            )
            logger.warning("Zalo outbox #%s failed on attempt %s", row["id"], row["attempts"])
    return sent


async def outbox_loop() -> None:
    while True:
        try:
            await flush_outbox()
        except Exception:
            logger.exception("Zalo outbox loop failed")
        await asyncio.sleep(5)
