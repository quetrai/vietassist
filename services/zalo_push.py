from __future__ import annotations

import logging

import httpx

from core.config import settings

logger = logging.getLogger(__name__)
_TIMEOUT = httpx.Timeout(30.0)


async def send_message(external_id: str, text: str) -> bool:
    """Đẩy tin nhắn chủ động tới một Zalo user qua control channel nội bộ của gateway.

    Trả False (không raise) nếu gateway chưa bật hoặc không phản hồi, để một lần gửi
    lỗi không làm sập vòng lặp daily digest.
    """
    if not settings.zalo_enabled:
        return False
    url = f"http://127.0.0.1:{settings.zalo_control_port}/send"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                url,
                json={"to": external_id, "text": text},
                headers={"x-bridge-secret": settings.bridge_secret},
            )
            response.raise_for_status()
    except httpx.HTTPError:
        logger.warning("Không gửi được tin nhắn chủ động tới %s qua gateway", external_id)
        return False
    return True
