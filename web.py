from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from telegram import Update
from telegram.ext import Application

from ai import router
from ai.contracts import ProviderError, ProviderUnavailable
from bot import build_application
from channels.zalo import ZaloEvent, download_image, is_group_command, resolve_user, summarize_group
from channels.zoom import ZoomEvent, parse_event as parse_zoom_event
from channels.zoom import resolve_user as resolve_zoom_user
from channels.zoom import send_message as send_zoom_message
from channels.zoom import verify_webhook_token as verify_zoom_webhook_token
from core import database, knowledge
from core.config import settings
from core.models import Channel, User
from services import commands, zalo_groups
from services.chat import chat
from services.maintenance import cleanup_loop
from services.prompt_engine import build_image_prompt_instruction
from services.reminders import reminder_loop
from services.zalo_digest import daily_digest_loop
from stock.market import close as close_stock

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


class _HealthCheckLogFilter(logging.Filter):
    """Ẩn access log uvicorn cho các lượt gọi liveness (`/`, `/health`, `/ready`).

    UptimeRobot (xem README, mục Deploy) ping `/health` mỗi 5 phút (mức tối
    thiểu của plan free) để Render không cho service ngủ — nếu không lọc, mỗi
    tháng có thêm hàng nghìn dòng "GET /health 200" vô nghĩa chen vào log,
    làm khó tìm log lỗi thật khi debug sự cố.
    """

    _QUIET_PATHS = ("GET / ", "HEAD / ", "GET /health", "HEAD /health", "GET /ready", "HEAD /ready")

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not any(needle in message for needle in self._QUIET_PATHS)


logging.getLogger("uvicorn.access").addFilter(_HealthCheckLogFilter())
telegram: Application | None = None
_DEFAULT_IMAGE_INSTRUCTION = "Tái tạo chính xác ảnh tham chiếu thành prompt tiếng Anh, giữ nguyên bố cục, dáng, ánh sáng, camera và chất ảnh."
_notified_unpaired_senders: set[str] = set()


class BridgeEvent(BaseModel):
    event_id: str = Field(min_length=1, max_length=256)
    kind: Literal["direct", "group", "image"]
    sender_id: str = Field(default="", max_length=256)
    sender_name: str = Field(default="", max_length=256)
    text: str = Field(default="", max_length=20000)
    group_id: str | None = Field(default=None, max_length=256)
    message_id: str | None = Field(default=None, max_length=256)
    image_url: str | None = Field(default=None, max_length=2048)


class BridgeReply(BaseModel):
    messages: list[str]


class ZaloSessionPayload(BaseModel):
    cookie_json: str = Field(min_length=1, max_length=200000)
    imei: str = Field(min_length=1, max_length=256)
    user_agent: str = Field(default="Mozilla/5.0", max_length=512)


class ZaloQrPayload(BaseModel):
    image_base64: str = Field(min_length=1, max_length=2_800_000)


class ZaloLoginResultPayload(BaseModel):
    ok: bool
    message: str = Field(default="", max_length=1000)


def _check_bridge_secret(x_bridge_secret: str) -> None:
    if not settings.bridge_secret or not hmac.compare_digest(
        x_bridge_secret, settings.bridge_secret
    ):
        raise HTTPException(403)


async def _startup_reindex() -> None:
    try:
        result = await knowledge.reindex()
        logger.info("Knowledge base reindex lúc khởi động: %s", result)
    except Exception:
        logger.exception("Lỗi reindex knowledge base lúc khởi động")


@asynccontextmanager
async def lifespan(_: FastAPI):
    global telegram
    settings.validate(webhook=True)
    await database.migrate()
    telegram = build_application()
    await telegram.initialize()
    await telegram.start()
    await telegram.bot.set_webhook(
        settings.webhook_base_url.rstrip("/") + "/webhook",
        secret_token=settings.webhook_secret,
        allowed_updates=["message", "callback_query"],
    )
    digest_task = asyncio.create_task(daily_digest_loop()) if settings.zalo_enabled else None
    reminder_task = asyncio.create_task(reminder_loop(telegram.bot))
    reindex_task = asyncio.create_task(_startup_reindex())
    cleanup_task = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        for task in (reminder_task, reindex_task, cleanup_task):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if digest_task is not None:
            digest_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await digest_task
        await telegram.stop()
        await telegram.shutdown()
        await router.close()
        await close_stock()
        await database.close()
        telegram = None


app = FastAPI(title="VietAssist", lifespan=lifespan)


@app.api_route("/", methods=["GET", "HEAD"])
@app.api_route("/health", methods=["GET", "HEAD"])
async def health() -> dict[str, str]:
    """Lightweight liveness endpoint for Render/UptimeRobot."""
    return {"status": "ok"}


@app.api_route("/ready", methods=["GET", "HEAD"])
async def readiness() -> dict[str, str]:
    if telegram is None:
        raise HTTPException(503, "Telegram chưa sẵn sàng")
    try:
        db = await database.pool()
        await db.fetchval("SELECT 1")
    except Exception as exc:
        logger.exception("Readiness check failed")
        raise HTTPException(503, "Database chưa sẵn sàng") from exc
    return {"status": "ready", "database": "ok", "telegram": "ok"}


@app.post("/webhook")
async def webhook(request: Request) -> Response:
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(secret, settings.webhook_secret) or telegram is None:
        raise HTTPException(403)
    payload = await request.json()
    event_id = str(payload.get("update_id", ""))
    if not event_id:
        raise HTTPException(400, "Thiếu update_id")
    lease_token = await database.claim_event(Channel.TELEGRAM, event_id)
    if lease_token is None:
        return Response(status_code=200)
    try:
        await telegram.process_update(Update.de_json(payload, telegram.bot))
    except Exception as exc:
        with contextlib.suppress(Exception):
            await database.fail_event(
                Channel.TELEGRAM, event_id, lease_token, f"{type(exc).__name__}: {exc}"
            )
        logger.exception("Telegram event %s failed", event_id)
        raise HTTPException(503, "Temporary processing failure") from exc
    await database.complete_event(Channel.TELEGRAM, event_id, lease_token)
    return Response(status_code=200)


@app.post("/webhook/zoom")
async def webhook_zoom(request: Request) -> Response:
    if not settings.zoom_enabled:
        raise HTTPException(404)
    if not verify_zoom_webhook_token(request.headers.get("authorization", "")):
        raise HTTPException(403)
    payload = await request.json()
    event = parse_zoom_event(payload)
    if event is None:
        # Sự kiện không phải tin nhắn/slash command (vd bot bị thêm vào channel) — bỏ
        # qua êm, không phải lỗi.
        return Response(status_code=200)
    lease_token = await database.claim_event(Channel.ZOOM, event.event_id)
    if lease_token is None:
        return Response(status_code=200)
    try:
        await _handle_zoom_event(event)
    except Exception as exc:
        with contextlib.suppress(Exception):
            await database.fail_event(
                Channel.ZOOM, event.event_id, lease_token, f"{type(exc).__name__}: {exc}"
            )
        logger.exception("Zoom event %s failed", event.event_id)
        raise HTTPException(503, "Temporary processing failure") from exc
    await database.complete_event(Channel.ZOOM, event.event_id, lease_token)
    return Response(status_code=200)


@app.post("/bridge/events", response_model=BridgeReply)
async def bridge_event(
    payload: BridgeEvent, x_bridge_secret: str = Header(default="")
) -> BridgeReply:
    _check_bridge_secret(x_bridge_secret)
    lease_token = await database.claim_event(Channel.ZALO, payload.event_id)
    if lease_token is None:
        return BridgeReply(messages=[])
    event = ZaloEvent(**payload.model_dump())
    try:
        result = await _handle_zalo_event(event)
    except Exception as exc:
        with contextlib.suppress(Exception):
            await database.fail_event(
                Channel.ZALO, event.event_id, lease_token, f"{type(exc).__name__}: {exc}"
            )
        logger.exception("Zalo event %s failed", event.event_id)
        raise HTTPException(503, "Temporary processing failure") from exc
    await database.complete_event(Channel.ZALO, event.event_id, lease_token)
    return result


@app.get("/bridge/zalo-session")
async def get_zalo_session(x_bridge_secret: str = Header(default="")) -> dict[str, str]:
    _check_bridge_secret(x_bridge_secret)
    return await database.get_zalo_session() or {}


@app.post("/bridge/zalo-session")
async def post_zalo_session(
    payload: ZaloSessionPayload, x_bridge_secret: str = Header(default="")
) -> dict[str, bool]:
    _check_bridge_secret(x_bridge_secret)
    await database.save_zalo_session(payload.cookie_json, payload.imei, payload.user_agent)
    return {"ok": True}


@app.post("/bridge/zalo-qr")
async def post_zalo_qr(
    payload: ZaloQrPayload, x_bridge_secret: str = Header(default="")
) -> dict[str, bool]:
    _check_bridge_secret(x_bridge_secret)
    try:
        image_bytes = base64.b64decode(payload.image_base64, validate=True)
    except ValueError as exc:
        raise HTTPException(400, "QR payload không hợp lệ") from exc
    if telegram is not None and settings.telegram_owner_id:
        with contextlib.suppress(Exception):
            await telegram.bot.send_photo(
                chat_id=settings.telegram_owner_id,
                photo=image_bytes,
                caption="Quét mã QR này bằng app Zalo (tài khoản B) để đăng nhập. Mã có thể hết hạn sau ít phút.",
            )
    return {"ok": True}


@app.post("/bridge/zalo-login-result")
async def post_zalo_login_result(
    payload: ZaloLoginResultPayload, x_bridge_secret: str = Header(default="")
) -> dict[str, bool]:
    _check_bridge_secret(x_bridge_secret)
    if telegram is not None and settings.telegram_owner_id:
        text = (
            "✅ Đăng nhập Zalo B thành công, bot đã sẵn sàng nhận tin."
            if payload.ok
            else f"❌ Đăng nhập Zalo B thất bại: {payload.message}"
        )
        with contextlib.suppress(Exception):
            await telegram.bot.send_message(chat_id=settings.telegram_owner_id, text=text)
    return {"ok": True}


async def _notify_owner_unpaired_sender(sender_id: str, sender_name: str) -> None:
    if not sender_id or sender_id in _notified_unpaired_senders:
        return
    if telegram is None or not settings.telegram_owner_id:
        return
    _notified_unpaired_senders.add(sender_id)
    ten = f" ({sender_name})" if sender_name else ""
    text = (
        f"🔔 Zalo id={sender_id}{ten} vừa nhắn cho Zalo B nhưng chưa được cấp quyền.\n"
        f"Dùng /zaloadmin {sender_id} để đặt làm admin, hoặc /zalopair {sender_id} để cấp quyền user."
    )
    with contextlib.suppress(Exception):
        await telegram.bot.send_message(chat_id=settings.telegram_owner_id, text=text)


async def _notify_owner_unpaired_zoom_sender(sender_jid: str) -> None:
    if not sender_jid or sender_jid in _notified_unpaired_senders:
        return
    if telegram is None or not settings.telegram_owner_id:
        return
    _notified_unpaired_senders.add(sender_jid)
    text = (
        f"🔔 Zoom jid={sender_jid} vừa nhắn cho bot nhưng chưa được cấp quyền.\n"
        f"Dùng /zoompair {sender_jid} để cấp quyền."
    )
    with contextlib.suppress(Exception):
        await telegram.bot.send_message(chat_id=settings.telegram_owner_id, text=text)


async def _handle_zoom_event(event: ZoomEvent) -> None:
    text = event.text.strip()
    user = await resolve_zoom_user(event.sender_jid)
    if user is None:
        # Chưa pair: im lặng với sender (không lộ ra là bot chưa cấu hình xong), chỉ
        # âm thầm báo cho Telegram owner, giống hệt nguyên tắc của kênh Zalo.
        await _notify_owner_unpaired_zoom_sender(event.sender_jid)
        return
    if not user.active:
        await send_zoom_message(event.to_jid, "Tài khoản đang bị tạm khóa.")
        return
    command_result = await commands.handle(user, text)
    if command_result is not None:
        await send_zoom_message(event.to_jid, command_result)
        return
    quote = await commands.try_ticker_quote(text)
    if quote is not None:
        await send_zoom_message(event.to_jid, quote)
        return
    result, provider = await chat(user, text)
    await send_zoom_message(event.to_jid, f"{result}\n\n⚙️ {provider}")


async def _handle_zalo_event(event: ZaloEvent) -> BridgeReply:
    text = event.text.strip()
    if event.kind == "group":
        if not event.group_id or not event.message_id:
            return BridgeReply(messages=[])
        if is_group_command(text):
            user = await resolve_user(event.sender_id)
            if user is None or not user.active:
                return BridgeReply(messages=[])
            return await _handle_group_command(user, text)
        await database.zalo_register_group(event.group_id)
        db = await database.pool()
        await db.execute(
            """INSERT INTO zalo_group_messages(group_id,external_message_id,sender_name,content,sent_at)
            SELECT $1,$2,$3,$4,NOW() FROM zalo_groups WHERE group_id=$1 AND enabled
            ON CONFLICT DO NOTHING""",
            event.group_id,
            event.message_id,
            event.sender_name,
            event.text,
        )
        return BridgeReply(messages=[])
    user = await resolve_user(event.sender_id)
    if user is None:
        # Chưa pair: Zalo B coi như tài khoản Zalo bình thường, KHÔNG tự động trả lời
        # gì cho người lạ (không lộ ra là bot, chủ tài khoản vẫn dùng app Zalo như
        # thường). Chỉ âm thầm báo cho Telegram owner để chủ động pair nếu muốn.
        await _notify_owner_unpaired_sender(event.sender_id, event.sender_name)
        return BridgeReply(messages=[])
    if not user.active:
        return BridgeReply(messages=["Tài khoản đang bị tạm khóa."])
    if event.kind == "image":
        return await _handle_zalo_image(event)
    command_result = await commands.handle(user, text)
    if command_result is not None:
        return BridgeReply(messages=[command_result])
    quote = await commands.try_ticker_quote(text)
    if quote is not None:
        return BridgeReply(messages=[quote])
    result, provider = await chat(user, text)
    return BridgeReply(messages=[result, f"⚙️ {provider}"])


async def _handle_zalo_image(event: ZaloEvent) -> BridgeReply:
    """Xử lý ảnh gửi trực tiếp cho Zalo B: tải về, đưa qua Gemini vision để viết prompt tái
    tạo — tương đương tính năng gửi ảnh trên Telegram. Chỉ áp dụng cho chat 1-1 (xem
    zalo-gateway/src/index.ts, ảnh trong group không được forward dạng 'image')."""
    if not event.image_url:
        return BridgeReply(messages=["Không nhận được ảnh, thử gửi lại."])
    instruction, spec = build_image_prompt_instruction(event.text.strip())
    try:
        path = await download_image(event.image_url)
    except httpx.HTTPError:
        return BridgeReply(messages=["Không tải được ảnh từ Zalo, thử gửi lại."])
    try:
        response = await router.image_prompt(path, instruction)
    except ProviderUnavailable:
        return BridgeReply(messages=["Chưa cấu hình Google Gemini để xử lý ảnh."])
    except ProviderError:
        return BridgeReply(messages=["Không xử lý được ảnh lúc này (lỗi tạm thời), thử lại sau."])
    finally:
        Path(path).unlink(missing_ok=True)
    return BridgeReply(messages=[f"📝 Prompt gợi ý\\n{spec.hint}\\n\\n{response.text.strip()}"])


async def _handle_group_command(user: User, text: str) -> BridgeReply:
    cmd, *args = text.split()
    cmd = cmd.lower()
    if not user.can_use_group_summary:
        message = (
            "Tính năng tổng kết nhóm chỉ dành cho quản trị viên."
            if cmd == "/tongket"
            else "Tính năng nhóm chỉ dành cho quản trị viên."
        )
        return BridgeReply(messages=[message])
    if cmd == "/tongket":
        if not args:
            return BridgeReply(messages=["Cú pháp: /tongket <nhóm> [24h|7d]"])
        result = await summarize_group(user, args[0], args[1] if len(args) > 1 else "24h")
        return BridgeReply(messages=[result])
    if cmd in ("/nhom", "/nhomzalo"):
        return BridgeReply(messages=[await zalo_groups.list_groups()])
    if cmd == "/themnhom":
        group_id = args[0] if args else ""
        alias = args[1] if len(args) > 1 else ""
        return BridgeReply(messages=[await zalo_groups.add_group(group_id, alias)])
    if cmd == "/xoanhom":
        identifier = args[0] if args else ""
        return BridgeReply(messages=[await zalo_groups.remove_group(identifier)])
    return BridgeReply(messages=["Lệnh nhóm chưa được hỗ trợ."])
