from __future__ import annotations

import asyncio

import httpx

from core.config import settings

_TIMEOUT = httpx.Timeout(5.0)
_RETRIES = 6
_RETRY_DELAY_SEC = 2.0


async def start_login() -> str:
    """Yêu cầu zalo-gateway (Node) bắt đầu đăng nhập QR cho Zalo B. Ảnh QR + kết quả đăng
    nhập sẽ được gateway gửi ngược lại Telegram owner qua các endpoint /bridge/zalo-qr và
    /bridge/zalo-login-result (xem web.py) — không đợi kết quả tại đây."""
    if not settings.zalo_enabled:
        return "ZALO_ENABLED=false — bật biến này rồi deploy lại trước khi đăng nhập Zalo B."
    if not settings.bridge_secret:
        return "Thiếu BRIDGE_SECRET, không thể gọi zalo-gateway."
    url = f"http://127.0.0.1:{settings.zalo_control_port}/login/start"
    last_error: Exception | None = None
    for attempt in range(1, _RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.post(url, headers={"x-bridge-secret": settings.bridge_secret})
            response.raise_for_status()
            return "Đã yêu cầu đăng nhập Zalo B — chờ mã QR gửi tới đây trong giây lát."
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < _RETRIES:
                await asyncio.sleep(_RETRY_DELAY_SEC)

    return f"Không gọi được zalo-gateway sau {_RETRIES} lần thử (127.0.0.1:{settings.zalo_control_port}): {last_error}"
