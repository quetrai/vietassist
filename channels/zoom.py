from __future__ import annotations

import asyncio
import hmac
import logging
import time
from dataclasses import dataclass

import httpx

from core import database
from core.config import settings
from core.models import User

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://api.zoom.us/oauth/token"
_MESSAGE_URL = "https://api.zoom.us/v2/im/chat/messages"
_HTTP_TIMEOUT = httpx.Timeout(15.0)

_token_lock = asyncio.Lock()
_cached_token: str | None = None
_cached_token_expiry: float = 0.0


@dataclass(frozen=True)
class ZoomEvent:
    event_id: str
    sender_jid: str
    text: str
    to_jid: str
    channel_name: str = ""


def verify_webhook_token(authorization_header: str) -> bool:
    """Xác thực webhook Zoom gửi tới bằng Verification Token cấu hình trên Marketplace
    (header Authorization == ZOOM_VERIFICATION_TOKEN nguyên văn — cơ chế xác thực của app
    kiểu 'General App + Chatbot' như quickstart zoom/chatbot-nodejs-quickstart dùng, KHÔNG
    có tiền tố "Bearer "). Nếu Marketplace app của bạn dùng Event Subscription kiểu mới hơn
    (chữ ký HMAC qua header x-zm-signature/x-zm-request-timestamp với 1 Secret Token riêng,
    kèm bước xác thực challenge-response endpoint.url_validation) thì hàm này CẦN được thay
    bằng xác thực chữ ký tương ứng — kiểm tra lại phần Feature > Chatbot trên Marketplace app
    của bạn xem đang dùng cơ chế nào trước khi bật ZOOM_ENABLED=true trên production."""
    if not settings.zoom_verification_token:
        return False
    return hmac.compare_digest(authorization_header, settings.zoom_verification_token)


async def _fetch_access_token() -> tuple[str, int]:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        response = await client.post(
            _TOKEN_URL,
            params={"grant_type": "client_credentials"},
            auth=(settings.zoom_client_id, settings.zoom_client_secret),
        )
    response.raise_for_status()
    payload = response.json()
    return payload["access_token"], int(payload.get("expires_in", 3600))


async def _access_token() -> str:
    """OAuth server-to-server (client_credentials) — cache theo TTL trừ hao 60s để tránh
    dùng token vừa hết hạn giữa lúc gọi API gửi tin nhắn."""
    global _cached_token, _cached_token_expiry
    async with _token_lock:
        if _cached_token and time.monotonic() < _cached_token_expiry:
            return _cached_token
        token, expires_in = await _fetch_access_token()
        _cached_token = token
        _cached_token_expiry = time.monotonic() + max(60, expires_in - 60)
        return token


async def send_message(to_jid: str, text: str) -> None:
    token = await _access_token()
    body = {
        "robot_jid": settings.zoom_bot_jid,
        "to_jid": to_jid,
        "account_id": settings.zoom_account_id,
        "content": {"body": [{"type": "message", "text": text}]},
    }
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        response = await client.post(
            _MESSAGE_URL, json=body, headers={"Authorization": f"Bearer {token}"}
        )
    response.raise_for_status()


def parse_event(payload: dict[str, object]) -> ZoomEvent | None:
    """Rút sự kiện tin nhắn/slash command từ webhook payload Zoom.

    LƯU Ý QUAN TRỌNG: tên field chính xác trong payload thật (userJid/user_jid,
    toJid/to_jid, cmd/message...) phụ thuộc loại app + phiên bản event Zoom gửi cho app cụ
    thể của bạn — có thể khác so với những gì hàm này giả định. Dùng tính năng gửi sự kiện
    thử trên Marketplace (mục Feature > Chatbot > Bot Endpoint URL) để xem đúng payload thật
    app nhận được, rồi chỉnh lại danh sách field bên dưới nếu cần trước khi deploy thật."""
    event_payload = payload.get("payload")
    if not isinstance(event_payload, dict):
        return None
    text = str(
        event_payload.get("cmd") or event_payload.get("message") or event_payload.get("content") or ""
    ).strip()
    sender_jid = str(
        event_payload.get("userJid") or event_payload.get("user_jid") or ""
    ).strip()
    to_jid = str(
        event_payload.get("toJid") or event_payload.get("to_jid") or sender_jid
    ).strip()
    channel_name = str(
        event_payload.get("channelName") or event_payload.get("channel_name") or ""
    ).strip()
    event_id = str(
        event_payload.get("messageId")
        or event_payload.get("message_id")
        or f"{sender_jid}:{payload.get('event_ts', '')}"
    ).strip()
    if not sender_jid or not text or not event_id:
        return None
    return ZoomEvent(event_id, sender_jid, text, to_jid, channel_name)


async def resolve_user(sender_jid: str) -> User | None:
    return await database.zoom_lookup(sender_jid)
