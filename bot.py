from __future__ import annotations

import contextlib
import html
import logging
import tempfile
from pathlib import Path

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
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
from services import commands, zalo_admin, zalo_login, zoom_admin
from services.chat import chat
from services.prompt_engine import build_image_prompt_instruction
from services.tg_format import reply_rich
from services.tg_format_codeblock import reply_code_block
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

ZOOM_ADMIN_COMMANDS = {
    "zoompair": zoom_admin.pair,
    "zoomkhoa": zoom_admin.lock,
    "zoommokhoa": zoom_admin.unlock,
    "zoomxoa": zoom_admin.remove,
}
ZOOM_ADMIN_COMMANDS_WITH_NAME = {"zoompair"}


async def telegram_user(update: Update) -> User:
    assert update.effective_user is not None
    return await database.get_or_create_user(
        Channel.TELEGRAM, str(update.effective_user.id), Role.ROOT
    )


def parse_zalo_admin_args(text: str) -> tuple[str, str]:
    """Tách `/lệnh <id> [tên...]` thành (external_id, tên hiển thị) — dùng chung cho cả
    lệnh quản trị Zalo và Zoom (chỉ tách chuỗi theo khoảng trắng, không phụ thuộc kênh)."""
    _, _, rest = text.strip().partition(" ")
    external_id, _, name = rest.strip().partition(" ")
    return external_id, name.strip()


_SPECIAL_COMMANDS_HELP = [
    "Gửi ảnh kèm mô tả để chuyển ảnh thành prompt.",
    "Quản trị Zalo: /zalopair, /zaloadmin, /zalokhoa, /zalomokhoa, /zaloxoa, /zalodanhsach.",
    "Quản trị Zoom: /zoompair, /zoomkhoa, /zoommokhoa, /zoomxoa, /zoomdanhsach.",
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


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Alias của /start — /help là tên người dùng có xu hướng gõ theo phản xạ
    # (Telegram, hầu hết app khác) khi không nhớ lệnh nào, nên trỏ về cùng
    # nội dung hướng dẫn thay vì bắt nhớ riêng "/start".
    await update.effective_message.reply_text(_start_text())


# Mô tả ngắn hiển thị trong menu lệnh gốc của Telegram (nút "/" cạnh ô nhập
# tin nhắn) — KHÔNG lặp lại "/tên_lệnh" vì Telegram đã hiển thị tên lệnh riêng.
_MENU_DESCRIPTIONS: dict[str, str] = {
    "start": "Xem hướng dẫn dùng bot",
    "help": "Xem hướng dẫn dùng bot (giống /start)",
    "gia": "Tra giá bán hiện tại, vd: /gia iphone 16",
    "prompt": "Viết prompt tạo ảnh từ mô tả",
    "stock": "Phân tích cổ phiếu, vd: /stock FPT sâu",
    "vimo": "Tin tức/vĩ mô thị trường",
    "quote": "Tra nhanh giá cổ phiếu theo mã",
    "ghichu": "Lưu ghi chú — để trống để xem danh sách",
    "dsghichu": "Xem danh sách ghi chú",
    "xoaghichu": "Xoá ghi chú theo id",
    "nhac": "Đặt nhắc nhở — để trống để xem danh sách",
    "dsnhac": "Xem danh sách nhắc nhở",
    "xoanhac": "Xoá nhắc nhở theo id",
    "muavao": "Ghi nhận mua cổ phiếu vào danh mục",
    "banra": "Ghi nhận bán cổ phiếu",
    "xoadanhmuc": "Xoá mã khỏi danh mục",
    "muctieu": "Đặt mức stop/target tham khảo cho 1 mã đang giữ",
    "danhmuc": "Xem danh mục đầu tư",
    "rag": "Bật/tắt tra cứu knowledge base cho chat",
    "ai": "Bật/tắt dùng 9Router làm model mặc định cho chat tự do",
    "nhom": "Xem danh sách nhóm Zalo đã bật allowlist (chỉ admin)",
    "nhomzalo": "Giống /nhom (chỉ admin)",
    "themnhom": "Thêm nhóm Zalo vào allowlist (chỉ admin)",
    "xoanhom": "Gỡ nhóm Zalo khỏi allowlist (chỉ admin)",
    "tongket": "Nhóm Zalo đang thảo luận gì (tóm tắt AI, chỉ admin)",
    "dangnoi": "Xem nguyên văn thảo luận trong ngày hôm nay của nhóm Zalo (chỉ admin)",
    "zalopair": "Cấp quyền cho 1 người dùng Zalo",
    "zaloadmin": "Cấp quyền admin nhóm Zalo",
    "zalokhoa": "Khoá 1 người dùng Zalo",
    "zalomokhoa": "Mở khoá 1 người dùng Zalo",
    "zaloxoa": "Xoá quyền 1 người dùng Zalo",
    "zalodanhsach": "Xem danh sách người dùng Zalo đã pair",
    "zalologin": "Đăng nhập Zalo B qua mã QR",
    "kbreindex": "Tính lại embedding knowledge base",
    "zoompair": "Cấp quyền cho 1 người dùng Zoom",
    "zoomkhoa": "Khoá 1 người dùng Zoom",
    "zoommokhoa": "Mở khoá 1 người dùng Zoom",
    "zoomxoa": "Xoá quyền 1 người dùng Zoom",
    "zoomdanhsach": "Xem danh sách người dùng Zoom đã pair",
}

# Thứ tự cố định (không dùng dict.keys() của COMMANDS/ZALO_ADMIN_COMMANDS trực
# tiếp) để lệnh hay dùng nhất (stock, quote, gia...) hiện lên đầu menu Telegram
# thay vì theo thứ tự khai báo module.
_MENU_ORDER: list[str] = [
    "help", "stock", "quote", "gia", "vimo", "prompt",
    "ghichu", "nhac", "danhmuc", "muavao", "banra", "muctieu",
    "xoaghichu", "xoanhac", "xoadanhmuc", "dsghichu", "dsnhac",
    "rag", "ai", "start",
    "zalopair", "zaloadmin", "zalokhoa", "zalomokhoa", "zaloxoa", "zalodanhsach",
    "zalologin", "kbreindex",
    "zoompair", "zoomkhoa", "zoommokhoa", "zoomxoa", "zoomdanhsach",
    "nhom", "nhomzalo", "themnhom", "xoanhom", "tongket", "dangnoi",
]


def _bot_commands() -> list[BotCommand]:
    known = (
        set(commands.COMMANDS)
        | {f"/{name}" for name in ZALO_ADMIN_COMMANDS}
        | {f"/{name}" for name in ZOOM_ADMIN_COMMANDS}
        | {
            "/zalodanhsach", "/zalologin", "/kbreindex", "/start", "/help",
            "/zoomdanhsach",
        }
    )
    ordered = [f"/{name}" for name in _MENU_ORDER]
    # An toàn cho tương lai: lệnh mới thêm vào COMMANDS mà quên cập nhật
    # _MENU_ORDER vẫn xuất hiện trong menu (cuối danh sách) thay vì âm thầm biến mất.
    ordered += sorted(known - set(ordered))
    return [
        BotCommand(name.lstrip("/"), _MENU_DESCRIPTIONS.get(name.lstrip("/"), name))
        for name in ordered
        if name in known
    ]


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
    if cmd.lower() == "/prompt" and result.startswith("📝") and "\n\n" in result:
        # Prompt tạo ảnh tiếng Anh hay chứa nhiều dấu *, _ (vd "--ar 4:5", "snake_case")
        # — reply_rich() bên dưới sẽ hiểu nhầm thành markdown bold/italic và nuốt mất ký
        # tự. Tách header khỏi phần thân, gửi thân trong khối <pre> (services/
        # tg_format_codeblock.py) để giữ nguyên văn + tiện bấm Copy trên Telegram.
        header, _, body = result.partition("\n\n")
        await update.effective_message.reply_text(html.escape(header), parse_mode="HTML")
        await reply_code_block(update.effective_message, body)
        return
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


async def call_zoom_admin_handler(cmd: str, external_id: str, name: str) -> str:
    handler = ZOOM_ADMIN_COMMANDS[cmd]
    if cmd in ZOOM_ADMIN_COMMANDS_WITH_NAME:
        return await handler(external_id, name)
    return await handler(external_id)


async def zoom_manage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.effective_message.text or ""
    cmd = text.strip().partition(" ")[0].lower().lstrip("/")
    external_id, name = parse_zalo_admin_args(text)
    result = await call_zoom_admin_handler(cmd, external_id, name)
    await update.effective_message.reply_text(result)


async def zoom_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(await zoom_admin.list_users())


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
    # Header gửi HTML thường (không phải <pre>) rồi prompt gửi RIÊNG trong khối <pre>,
    # để nếu bấm nút Copy của Telegram thì chỉ chép đúng phần prompt, không dính header.
    # Dùng services/tg_format_codeblock.py (không phải reply_rich) vì prompt tiếng Anh
    # hay chứa nhiều dấu *, _ (vd "--ar 4:5", "snake_case") — reply_rich sẽ hiểu nhầm
    # thành markdown bold/italic và nuốt mất ký tự, khiến prompt chép ra khác bản gốc.
    header = f"📝 Prompt gợi ý\n{hint}"
    await message_obj.reply_text(html.escape(header), parse_mode="HTML")
    await reply_code_block(message_obj, prompt)


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
    # Đăng ký menu lệnh gốc của Telegram (nút "/" cạnh ô nhập tin nhắn) — trước
    # đây chỉ có /start liệt kê lệnh dạng text, người dùng phải nhớ gõ /start
    # để xem; giờ toàn bộ lệnh + mô tả ngắn hiện sẵn trong menu, gõ "/" là thấy.
    with contextlib.suppress(Exception):
        await app.bot.set_my_commands(_bot_commands())


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
    app.add_handler(CommandHandler("help", help_command, filters=owner))
    for name in commands.COMMANDS:
        if name == "/help":
            continue  # Telegram đã có CommandHandler("help", ...) riêng ở trên.
        app.add_handler(CommandHandler(name.lstrip("/"), command, filters=owner))
    for name in ZALO_ADMIN_COMMANDS:
        app.add_handler(CommandHandler(name, zalo_manage, filters=owner))
    app.add_handler(CommandHandler("zalodanhsach", zalo_list, filters=owner))
    app.add_handler(CommandHandler("zalologin", zalo_login_start, filters=owner))
    app.add_handler(CommandHandler("kbreindex", kb_reindex, filters=owner))
    for name in ZOOM_ADMIN_COMMANDS:
        app.add_handler(CommandHandler(name, zoom_manage, filters=owner))
    app.add_handler(CommandHandler("zoomdanhsach", zoom_list, filters=owner))
    app.add_handler(CallbackQueryHandler(rag_toggle_callback, pattern=r"^rag:(on|off)$"))
    app.add_handler(MessageHandler(owner & (filters.PHOTO | filters.Document.IMAGE), image))
    app.add_handler(MessageHandler(owner & filters.TEXT & ~filters.COMMAND, message))

    app.add_handler(MessageHandler(~owner, deny_message), group=1)
    return app
