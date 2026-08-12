from __future__ import annotations

import asyncio
import hashlib
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
    """Xác thực webhook Zoom gửi tới bằng Verification Token CU (header Authorization ==
    ZOOM_VERIFICATION_TOKEN nguyen van, KHONG co tien to "Bearer "). Chi dung cho app kieu
    'General App + Chatbot' doi cu. App tao moi tren Marketplace (muc Access > Token >
    Secret Token, di cung Event Subscriptions) PHAI dung verify_webhook_signature() ben
    duoi thay vi ham nay."""
    if not settings.zoom_verification_token:
        return False
    return hmac.compare_digest(authorization_header, settings.zoom_verification_token)


def verify_webhook_signature(
    signature_header: str, timestamp_header: str, raw_body: bytes
) -> bool:
    """Xac thuc webhook Zoom bang chu ky HMAC-SHA256 (co che Secret Token + Event
    Subscriptions hien hanh tren Marketplace). Zoom ky message dang
    "v0:{timestamp}:{raw_body}" bang Secret Token, gui kem header:
      - x-zm-request-timestamp: timestamp dung de ky
      - x-zm-signature: "v0=" + hex digest
    Xem: https://developers.zoom.us/docs/api/webhooks/#verify-webhook-events"""
    if not settings.zoom_secret_token or not signature_header or not timestamp_header:
        return False
    message = f"v0:{timestamp_header}:{raw_body.decode('utf-8')}"
    computed_hash = hmac.new(
        settings.zoom_secret_token.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    expected_signature = f"v0={computed_hash}"
    return hmac.compare_digest(signature_header, expected_signature)


def build_url_validation_response(plain_token: str) -> dict:
    """Xay phan hoi cho buoc xac thuc challenge-response khi ban bam Validate tren
    Marketplace (event 'endpoint.url_validation'). Zoom POST payload chua plainToken,
    app phai tra lai {"plainToken": ..., "encryptedToken": HMAC-SHA256(plainToken)}
    ky bang Secret Token - KHONG can verify chu ky o buoc nay vi day la buoc thiet lap.
    Xem: https://developers.zoom.us/docs/api/webhooks/#validate-your-webhook-endpoint"""
    encrypted_token = hmac.new(
        settings.zoom_secret_token.encode("utf-8"),
        plain_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {"plainToken": plain_token, "encryptedToken": encrypted_token}


async def _fetch_access_token() -> tuple[str, int]:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        response = await client.post(
            _TOKEN_URL,
            params={
                "grant_type": "account_credentials",
                "account_id": settings.zoom_account_id,
            },
            auth=(settings.zoom_client_id, settings.zoom_client_secret),
        )
    response.raise_for_status()
    payload = response.json()
    return payload["access_token"], int(payload.get("expires_in", 3600))


async def _access_token() -> str:
    """Server-to-Server OAuth (account_credentials + account_id) — cache theo TTL trừ hao
    60s để tránh dùng token vừa hết hạn giữa lúc gọi API gửi tin nhắn."""
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
