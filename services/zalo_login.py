from __future__ import annotations

import httpx

from core.config import settings

_TIMEOUT = httpx.Timeout(10.0)


async def start_login() -> str:
    """Yêu cầu zalo-gateway (Node) bắt đầu đăng nhập QR cho Zalo B. Ảnh QR + kết quả đăng
    nhập sẽ được gateway gửi ngược lại Telegram owner qua các endpoint /bridge/zalo-qr và
    /bridge/zalo-login-result (xem web.py) — không đợi kết quả tại đây."""
    if not settings.zalo_enabled:
        return "ZALO_ENABLED=false — bật biến này rồi deploy lại trước khi đăng nhập Zalo B."
    if not settings.bridge_secret:
        return "Thiếu BRIDGE_SECRET, không thể gọi zalo-gateway."
    url = f"http://127.0.0.1:{settings.zalo_control_port}/login/start"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, headers={"x-bridge-secret": settings.bridge_secret})
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return (
            "Không gọi được zalo-gateway (kiểm tra ZALO_ENABLED, tiến trình Node đã chạy "
            f"chưa): {exc}"
        )
    return "Đã yêu cầu đăng nhập Zalo B — chờ mã QR gửi tới đây trong giây lát."
