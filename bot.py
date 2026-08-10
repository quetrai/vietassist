from __future__ import annotations

import contextlib
import html
import logging
import tempfile
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ai import router
from core import database, knowledge
from core.config import settings
from core.models import Channel, Role, User
from services import commands, zalo_admin, zalo_login
from services.chat import chat
from services.prompt_engine import build_image_prompt_instruction
from services.tg_format import reply_rich
from stock.market import close as close_stock

logger = logging.getLogger(__name__)
_MAX_IMAGE_BYTES = 10 * 1024 * 1024

ZALO_ADMIN_COMMANDS = {
    "zalopair": zalo_admin.pair,
    "zaloadmin": zalo_admin.set_admin,
    "zalokhoa": zalo_admin.lock,
    "zalomokhoa": zalo_admin.unlock,
    "zaloxoa": zalo_admin.remove,
}
ZALO_ADMIN_COMMANDS_WITH_NAME = {"zalopair", "zaloadmin"}


async def telegram_user(update: Update) -> User:
    assert update.effective_user is not None
    return await database.get_or_create_user(
        Channel.TELEGRAM, str(update.effective_user.id), Role.ROOT
    )


def parse_zalo_admin_args(text: str) -> tuple[str, str]:
    """Tách `/lệnh <id_zalo> [tên...]` thành (external_id, tên hiển thị)."""
    _, _, rest = text.strip().partition(" ")
    external_id, _, name = rest.strip().partition(" ")
    return external_id, name.strip()


_SPECIAL_COMMANDS_HELP = [
    "Gửi ảnh kèm mô tả để chuyển ảnh thành prompt.",
    "Quản trị Zalo: /zalopair, /zaloadmin, /zalokhoa, /zalomokhoa, /zaloxoa, /zalodanhsach.",
    "Đăng nhập Zalo B: /zalologin (sinh mã QR gửi ngay tại đây, quét bằng app Zalo B).",
    "Knowledge base: /kbreindex (tính lại embedding sau khi cập nhật tài liệu .md), "
    "/rag on|off (bật/tắt tra cứu — gõ /rag suông để bấm nút).",
]


def _start_text() -> str:
    lines = ["VietAssist sẵn sàng.", ""]
    lines.extend(entry.help for entry in commands.COMMANDS.values())
    lines.append("Mẹo: gõ đúng 3 chữ cái (vd FPT) để tra nhanh giá, không cần gõ /quote.")
    lines.extend(_SPECIAL_COMMANDS_HELP)
    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(_start_text())


def _rag_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(("✅ " if enabled else "") + "Bật", callback_data="rag:on"),
                InlineKeyboardButton(
                    ("✅ " if not enabled else "") + "Tắt", callback_data="rag:off"
                ),
            ]
        ]
    )


async def command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await telegram_user(update)
    text = update.effective_message.text or ""
    cmd, _, argument = text.strip().partition(" ")
    if cmd.lower() == "/rag" and not argument.strip():
        status = "BẬT ✅" if user.rag_enabled else "TẮT 🚫"
        await update.effective_message.reply_text(
            f"Tra cứu knowledge base (RAG) đang: {status}\n"
            "Bấm nút bên dưới để đổi, hoặc gõ /rag on | /rag off.",
            reply_markup=_rag_keyboard(user.rag_enabled),
        )
        return
    result = await commands.handle(user, text)
    # Kết quả lệnh (/stock, /gia, /vimo...) có thể chứa markdown do AI sinh ra
    # (**bold**, gạch đầu dòng...) — convert sang HTML để hiển thị đẹp trên Telegram.
    await reply_rich(update.effective_message, result or "Lệnh chưa được hỗ trợ.")


async def rag_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    # CallbackQueryHandler không nhận filters=owner như CommandHandler/MessageHandler nên
    # tự kiểm tra ở đây — tránh người lạ bấm được nút nếu lỡ thấy tin nhắn (group, forward...).
    if update.effective_user is None or update.effective_user.id != settings.telegram_owner_id:
        await query.answer("Bạn không có quyền dùng nút này.", show_alert=True)
        return
    await query.answer()
    enabled = query.data == "rag:on"
    user = await telegram_user(update)
    await database.set_rag_enabled(user.id, enabled)
    status = "BẬT ✅" if enabled else "TẮT 🚫"
    await query.edit_message_text(
        f"Tra cứu knowledge base (RAG) đang: {status}\n"
        "Bấm nút bên dưới để đổi, hoặc gõ /rag on | /rag off.",
        reply_markup=_rag_keyboard(enabled),
    )


async def call_zalo_admin_handler(cmd: str, external_id: str, name: str) -> str:
    handler = ZALO_ADMIN_COMMANDS[cmd]
    if cmd in ZALO_ADMIN_COMMANDS_WITH_NAME:
        return await handler(external_id, name)
    return await handler(external_id)


async def zalo_manage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.effective_message.text or ""
    cmd = text.strip().partition(" ")[0].lower().lstrip("/")
    external_id, name = parse_zalo_admin_args(text)
    result = await call_zalo_admin_handler(cmd, external_id, name)
    await update.effective_message.reply_text(result)


async def zalo_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(await zalo_admin.list_users())


async def zalo_login_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(await zalo_login.start_login())


async def kb_reindex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("Đang tính lại embedding, chờ chút...")
    await update.effective_message.reply_text(await knowledge.reindex())


async def message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await telegram_user(update)
    text = (update.effective_message.text or "").strip()
    if not text:
        return
    quote = await commands.try_ticker_quote(text)
    if quote is not None:
        await update.effective_message.reply_text(quote)
        return
    reply, provider = await chat(user, text)
    # Chat tự do (Groq/OpenRouter/Gemini) rất hay trả **bold**/`code`/gạch đầu dòng —
    # convert markdown->HTML thay vì hiện nguyên ký tự thô.
    await reply_rich(update.effective_message, f"{reply}\n\n⚙️ {provider}")


async def _reply_prompt(message_obj, prompt: str, hint: str) -> None:
    prompt = prompt.strip()
    if not prompt:
        await message_obj.reply_text("Gemini không trả về prompt. Vui lòng thử lại.")
        return
    # HTML <pre> giữ nguyên dấu *, _, backticks... và thuận tiện cho thao tác copy.
    header = f"📝 Prompt gợi ý\\n{hint}"
    escaped = html.escape(prompt)
    max_body = 3900
    if len(escaped) <= max_body:
        await message_obj.reply_text(
            f"{html.escape(header)}\\n\\n<pre>{escaped}</pre>",
            parse_mode="HTML",
        )
        return
    await message_obj.reply_text(html.escape(header), parse_mode="HTML")
    for start in range(0, len(escaped), max_body):
        await message_obj.reply_text(
            f"<pre>{escaped[start : start + max_body]}</pre>", parse_mode="HTML"
        )


async def image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message_obj = update.effective_message
    file_obj = None
    suffix = ".jpg"
    if message_obj.photo:
        file_obj = await message_obj.photo[-1].get_file()
    elif message_obj.document and getattr(message_obj.document, "mime_type", "").startswith(
        "image/"
    ):
        file_obj = await message_obj.document.get_file()
        suffix = Path(message_obj.document.file_name or "image.jpg").suffix.lower() or ".jpg"
        if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            suffix = ".jpg"
    if file_obj is None:
        return

    file_size = getattr(file_obj, "file_size", None)
    if file_size is not None and file_size > _MAX_IMAGE_BYTES:
        await message_obj.reply_text("Ảnh quá lớn. Vui lòng gửi ảnh nhỏ hơn 10 MB.")
        return

    instruction, spec = build_image_prompt_instruction(message_obj.caption or "")
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing",
    )
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        path = handle.name
    try:
        await file_obj.download_to_drive(path)
        if Path(path).stat().st_size > _MAX_IMAGE_BYTES:
            await message_obj.reply_text(
                "Ảnh quá lớn sau khi tải xuống. Vui lòng gửi ảnh nhỏ hơn 10 MB."
            )
            return
        response = await router.image_prompt(path, instruction)
        await _reply_prompt(message_obj, response.text, spec.hint)
    except Exception:
        logger.exception("Lỗi chuyển ảnh thành prompt")
        await message_obj.reply_text(
            "Không thể chuyển ảnh thành prompt lúc này. Vui lòng thử lại sau."
        )
    finally:
        Path(path).unlink(missing_ok=True)


async def deny_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text("Bạn không có quyền sử dụng bot này.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bắt mọi exception chưa được xử lý trong handler, để không rơi vào im lặng: log lỗi và
    báo cho người dùng biết thay vì không phản hồi gì."""
    logger.exception("Lỗi chưa xử lý khi phục vụ update", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        with contextlib.suppress(Exception):
            await update.effective_message.reply_text(
                "Đã có lỗi xảy ra khi xử lý yêu cầu, vui lòng thử lại sau."
            )


async def post_init(app: Application) -> None:
    await database.migrate()


async def post_shutdown(app: Application) -> None:
    await router.close()
    await close_stock()
    await database.close()


def build_application() -> Application:
    app = (
        Application.builder()
        .token(settings.telegram_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_error_handler(on_error)

    owner = filters.User(user_id=settings.telegram_owner_id)

    app.add_handler(CommandHandler("start", start, filters=owner))
    for name in commands.COMMANDS:
        app.add_handler(CommandHandler(name.lstrip("/"), command, filters=owner))
    for name in ZALO_ADMIN_COMMANDS:
        app.add_handler(CommandHandler(name, zalo_manage, filters=owner))
    app.add_handler(CommandHandler("zalodanhsach", zalo_list, filters=owner))
    app.add_handler(CommandHandler("zalologin", zalo_login_start, filters=owner))
    app.add_handler(CommandHandler("kbreindex", kb_reindex, filters=owner))
    app.add_handler(CallbackQueryHandler(rag_toggle_callback, pattern=r"^rag:(on|off)$"))
    app.add_handler(MessageHandler(owner & (filters.PHOTO | filters.Document.IMAGE), image))
    app.add_handler(MessageHandler(owner & filters.TEXT & ~filters.COMMAND, message))

    app.add_handler(MessageHandler(~owner, deny_message), group=1)
    return app
