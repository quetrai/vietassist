from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
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
    account_id: str = ""

    @property
    def reply_jid(self) -> str:
        """JID dung de TRA LOI (khac voi to_jid cua su kien nhan vao).

        Theo docs Zoom, "toJid" trong webhook nghia la JID cua channel/user MA TIN
        NHAN DUOC GUI DEN - trong chat 1:1, do chinh la JID cua BOT (nguoi nhan tin),
        khong phai cua nguoi dung. Neu dung nguyen toJid de tra loi trong truong hop
        1:1, Zoom se tra 7004 "No channel or user can be found with the given to_jid"
        vi ban dang co gui tin nhan CHO CHINH BOT. Chi khi la kenh nhom (channel_name
        khac rong) thi toJid moi la dich hop le (JID cua channel) de tra loi ve."""
        return self.to_jid if self.channel_name else self.sender_jid


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
            params={"grant_type": "client_credentials"},
            auth=(settings.zoom_client_id, settings.zoom_client_secret),
        )
    if response.status_code >= 400:
        # Log rõ lý do Zoom từ chối (invalid_client, invalid_request...) thay vì chỉ
        # raise HTTPStatusError chung chung — giúp chẩn đoán nhanh sai client_id/secret/
        # scope hay app chưa activate.
        logger.error("Zoom OAuth token request failed (%s): %s", response.status_code, response.text)
    response.raise_for_status()
    payload = response.json()
    return payload["access_token"], int(payload.get("expires_in", 3600))


async def _access_token() -> str:
    """Chatbot token (grant_type=client_credentials, scope imchat:bot) — cache theo TTL
    trừ hao 60s để tránh dùng token vừa hết hạn giữa lúc gọi API gửi tin nhắn."""
    global _cached_token, _cached_token_expiry
    async with _token_lock:
        if _cached_token and time.monotonic() < _cached_token_expiry:
            return _cached_token
        token, expires_in = await _fetch_access_token()
        _cached_token = token
        _cached_token_expiry = time.monotonic() + max(60, expires_in - 60)
        return token


class ZoomSendError(RuntimeError):
    """Mot hoac nhieu doan cua tin nhan da cat khong gui duoc toi Zoom sau khi retry."""


_MAX_MESSAGE_CHARS = 4096  # Gioi han cua Zoom Team Chat cho 1 tin nhan (docs Zoom).

# Zoom Team Chat dung phuong ngu Markdown RIENG (giong Slack), khac han GFM ma cac
# model AI hay sinh ra:
#   - Bold:      *text*   (GFM dung **text**)
#   - Italic:    _text_   (giong nhau)
#   - Gach ngang: ~text~   (GFM dung ~~text~~)
#   - KHONG co header (#, ##, ###) va KHONG co bang (| a | b |) - Zoom hien nguyen
#     van cac ky tu do nhu text thuong, rat xau.
# Vi khong bat is_markdown_support, Zoom truoc gio con hien nguyen ca dau ** va #.
_MD_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_MD_TABLE_SEP_CELL = re.compile(r"^:?-{2,}:?$")
_MD_HEADER = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_STRIKE = re.compile(r"~~(.+?)~~")


def _to_zoom_markdown(text: str) -> str:
    """Chuyen markdown GFM (## header, **bold**, bang |a|b|) ma model AI hay sinh ra
    sang phuong ngu Markdown ma Zoom Team Chat thuc su ho tro. Dung cung voi
    is_markdown_support=True trong body gui len Zoom."""
    lines_out: list[str] = []
    for line in text.split("\n"):
        header_match = _MD_HEADER.match(line)
        table_match = _MD_TABLE_ROW.match(line)
        if header_match:
            lines_out.append(f"*{header_match.group(2).strip()}*")
            continue
        if table_match:
            cells = [c.strip() for c in table_match.group(1).split("|")]
            if all(_MD_TABLE_SEP_CELL.match(c) for c in cells if c):
                continue  # dòng phân cách "|---|---|" của bảng markdown - bỏ qua
            lines_out.append("• " + " — ".join(c for c in cells if c))
            continue
        lines_out.append(line)
    converted = "\n".join(lines_out)
    converted = _MD_BOLD.sub(r"*\1*", converted)
    converted = _MD_STRIKE.sub(r"~\1~", converted)
    return converted


async def _post_message_once(to_jid: str, text: str, user_jid: str, account_id: str) -> None:
    token = await _access_token()
    body = {
        "robot_jid": settings.zoom_bot_jid,
        "to_jid": to_jid,
        "user_jid": user_jid,
        "account_id": account_id,
        "is_markdown_support": True,
        "content": {"body": [{"type": "message", "text": text}]},
    }
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        response = await client.post(
            _MESSAGE_URL, json=body, headers={"Authorization": f"Bearer {token}"}
        )
    if response.status_code >= 400:
        # Log ro noi dung Zoom tra ve (vi du "text too long", sai to_jid, sai
        # account_id...) thay vi de raise_for_status() nuot mat ly do that su - giup
        # chan doan nhanh hon.
        logger.error(
            "Zoom send message failed (%s) to_jid=%s user_jid=%s account_id=%s: %s",
            response.status_code,
            to_jid,
            user_jid,
            account_id,
            response.text,
        )
    response.raise_for_status()


_CHUNK_RETRY_ATTEMPTS = 3
_CHUNK_RETRY_BACKOFF_SEC = 1.5


async def _post_message(to_jid: str, text: str, user_jid: str, account_id: str) -> None:
    # Neu tin nhan dai bi cat thanh nhieu chunk, mot loi tam thoi (429 rate limit, 5xx,
    # timeout mang...) o MOT chunk giua chung KHONG duoc phep lam mat cac chunk con lai
    # phia sau - nhung chunk truoc do da gui toi nguoi dung roi, neu loop dung luon thi
    # phan cuoi cau tra loi bien mat vinh vien ("mat phan cuoi khi doan dai"). Retry vai
    # lan truoc khi thuc su bo cuoc voi chunk nay.
    last_exc: Exception | None = None
    for attempt in range(1, _CHUNK_RETRY_ATTEMPTS + 1):
        try:
            await _post_message_once(to_jid, text, user_jid, account_id)
            return
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < _CHUNK_RETRY_ATTEMPTS:
                logger.warning(
                    "Zoom send message retry %s/%s to_jid=%s: %s",
                    attempt,
                    _CHUNK_RETRY_ATTEMPTS,
                    to_jid,
                    exc,
                )
                await asyncio.sleep(_CHUNK_RETRY_BACKOFF_SEC * attempt)
    assert last_exc is not None
    raise last_exc


def _split_message(text: str, limit: int) -> list[str]:
    """Cat text dai thanh nhieu doan <= limit ky tu, uu tien cat tai ranh gioi dong
    trong/xuong dong thay vi cat cung giua tu hoac giua cap dau markdown (*bold*, _ital_)
    - cat cung giua co the lam hong dinh dang nhung khong lam mat noi dung; van giu cat
    cung nhu phuong an du phong khi khong tim duoc ranh gioi hop ly."""
    if len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        split_at = window.rfind("\n\n")
        if split_at < limit // 2:
            split_at = window.rfind("\n")
        if split_at < limit // 2:
            split_at = window.rfind(" ")
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


async def send_message(
    to_jid: str, text: str, user_jid: str | None = None, account_id: str | None = None
) -> None:
    # user_jid (JID cua nguoi bot dang gui thay mat) la truong BAT BUOC theo Zoom
    # Chatbot API - thieu no Zoom tra ve 400 "Invalid request body format" (code 7001).
    # Mac dinh dung to_jid khi khong co user_jid rieng (vi du chat 1:1, to_jid chinh la
    # sender).
    effective_user_jid = user_jid or to_jid
    # account_id PHAI la accountId cua chinh webhook event chua to_jid nay - dung
    # settings.zoom_account_id (config tinh) khi khong lay duoc tu event lam fallback,
    # nhung uu tien gia tri tu event de tranh 7004 "No channel or user can be found".
    effective_account_id = account_id or settings.zoom_account_id
    # Convert markdown GFM (##, **, |bang|) model AI hay sinh sang phuong ngu Zoom
    # ho tro (*, _, khong header/bang) - neu khong Zoom hien nguyen van cac ky tu
    # ##, ** rat xau vi khong duoc parse.
    text = _to_zoom_markdown(text)
    # Zoom Team Chat tu choi (400) neu 1 tin nhan vuot qua 4096 ky tu. Cat nho thanh
    # nhieu tin thay vi gui nguyen mot khoi dai (vi du cau tra loi AI dai). Gui TUAN
    # TU (khong song song) de tin den dung thu tu, nhung KHONG dung han "chunk dau
    # tien sao thi chunk sau vay" - moi chunk gui doc lap, loi o mot chunk khong huy
    # cac chunk con lai.
    chunks = _split_message(text, _MAX_MESSAGE_CHARS)
    errors: list[str] = []
    for chunk in chunks:
        try:
            await _post_message(to_jid, chunk, effective_user_jid, effective_account_id)
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            errors.append(str(exc))
            logger.error(
                "Zoom send message: bo qua 1 doan sau khi het luot retry (van gui tiep "
                "cac doan con lai) to_jid=%s: %s",
                to_jid,
                exc,
            )
    if errors:
        raise ZoomSendError(
            f"Gui {len(errors)}/{len(chunks)} doan tin nhan Zoom that bai: " + "; ".join(errors)
        )


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
    # QUAN TRONG: accountId trong TUNG webhook event - KHONG dung settings.zoom_account_id
    # tinh khi goi lai API gui tin nhan, vi to_jid chi resolve dung trong dung ngu canh
    # account da phat sinh no. Dung sai account_id se khien Zoom tra 7004 "No channel or
    # user can be found with the given to_jid" du to_jid hoan toan hop le.
    account_id = str(
        event_payload.get("accountId") or event_payload.get("account_id") or ""
    ).strip()
    event_id = str(
        event_payload.get("messageId")
        or event_payload.get("message_id")
        or f"{sender_jid}:{payload.get('event_ts', '')}"
    ).strip()
    if not sender_jid or not text or not event_id:
        return None
    return ZoomEvent(event_id, sender_jid, text, to_jid, channel_name, account_id)


async def resolve_user(sender_jid: str) -> User | None:
    return await database.zoom_lookup(sender_jid)
