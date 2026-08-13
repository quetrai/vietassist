from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ai import router
from ai.contracts import GroundingUnavailable, ProviderError
from channels.zalo import summarize_group, today_discussion
from core import database
from core.models import User
from services import portfolio, reminders, translate as translate_service, zalo_groups
from services.chat import text_to_prompt
from services.prompt_engine import prompt_spec
from stock import analyze_symbol, quick_quote
from stock.analysis import normalize_symbol

_GROUP_FEATURE_DENIED = "Tính năng nhóm Zalo chỉ dành cho quản trị viên."

Handler = Callable[[User, str], Awaitable[str]]


@dataclass(frozen=True)
class Command:
    handler: Handler
    help: str


async def try_ticker_quote(text: str) -> str | None:
    candidate = text.strip()
    if len(candidate) != 3 or not candidate.isalpha() or not candidate.isascii():
        return None
    try:
        return await quick_quote(candidate)
    except (ValueError, RuntimeError):
        return None


async def _cmd_gia(user: User, argument: str) -> str:
    if not argument:
        return "Cú pháp: /gia <sản phẩm>"
    try:
        return (await router.product_search(argument)).text
    except GroundingUnavailable:
        return "Chưa cấu hình dịch vụ tìm kiếm thời gian thực."
    except ProviderError:
        return "Không tìm thấy dữ liệu giá thời gian thực đã được xác minh lúc này. Không dùng dữ liệu đoán."


async def _cmd_prompt(user: User, argument: str) -> str:
    if not argument:
        return "Cú pháp: /prompt <mô tả ảnh cần tạo>"
    try:
        result = (await text_to_prompt(user, argument))[0].strip()
        if not result:
            return "AI không trả về prompt, thử lại sau."
        return f"📝 Prompt gợi ý\n{prompt_spec(argument).hint}\n\n{result}"
    except ProviderError:
        return "Không tạo được prompt lúc này (provider AI đang lỗi hoặc hết hạn mức), thử lại sau."


async def _cmd_dich(user: User, argument: str) -> str:
    if not argument.strip():
        return (
            "Cú pháp: /dich [ja>vi|vi>ja] <nội dung>\n"
            "Không chỉ định chiều thì tự nhận diện theo chữ Nhật trong câu.\n"
            "Ví dụ: /dich お世話になります。確認お願いします。"
        )
    first_token, _, rest = argument.partition(" ")
    direction = translate_service.parse_explicit_direction(first_token)
    text = rest if direction else argument
    if not text.strip():
        return "Cú pháp: /dich [ja>vi|vi>ja] <nội dung>"
    try:
        result, _, resolved = await translate_service.translate(text, direction)
    except ValueError as exc:
        return f"Cú pháp: /dich [ja>vi|vi>ja] <nội dung>\n({exc})"
    except ProviderError:
        return "Không dịch được lúc này (provider AI đang lỗi hoặc hết hạn mức), thử lại sau."
    label = translate_service.direction_label(resolved)
    return f"🇯🇵↔🇻🇳 {label}\n\n{result}"


async def _cmd_stock(user: User, argument: str) -> str:
    if not argument:
        return "Cú pháp: /stock <MÃ> [sâu]"
    parts = argument.split()
    symbol_raw = parts[0]
    deep = len(parts) > 1 and parts[1].lower() in {"sau", "sâu"}
    holding = False
    try:
        holding = await database.is_holding(user.id, normalize_symbol(symbol_raw))
    except ValueError:
        pass
    try:
        return await analyze_symbol(symbol_raw, holding=holding, deep=deep)
    except (ValueError, RuntimeError) as exc:
        return f"Không phân tích được: {exc}"


async def _cmd_vimo(user: User, argument: str) -> str:
    if not argument:
        return "Cú pháp: /vimo <câu hỏi vĩ mô/tin tức thị trường>"
    try:
        return (await router.macro_news(argument)).text
    except GroundingUnavailable:
        return "Chưa cấu hình dịch vụ tìm kiếm thời gian thực."
    except ProviderError:
        return "Không tìm thấy dữ liệu thời gian thực đã được xác minh lúc này. Không dùng dữ liệu cũ hoặc tự bịa."


async def _cmd_quote(user: User, argument: str) -> str:
    if not argument:
        return "Cú pháp: /quote <MÃ>"
    try:
        return await quick_quote(argument.split()[0])
    except (ValueError, RuntimeError) as exc:
        return f"Không lấy được giá: {exc}"


async def _cmd_ghichu(user: User, argument: str) -> str:
    # Không gõ nội dung -> xem như muốn xem danh sách, đỡ phải nhớ thêm lệnh
    # /dsghichu riêng. /dsghichu vẫn giữ nguyên (alias) cho ai đã quen gõ nó.
    if not argument.strip():
        return await reminders.list_notes(user.id)
    return await reminders.add_note(user.id, argument)


async def _cmd_dsghichu(user: User, argument: str) -> str:
    return await reminders.list_notes(user.id)


async def _cmd_xoaghichu(user: User, argument: str) -> str:
    return await reminders.remove_note(user.id, argument.strip())


async def _cmd_nhac(user: User, argument: str) -> str:
    # Không gõ gì -> xem danh sách nhắc nhở, cùng nguyên tắc với /ghichu.
    # /dsnhac vẫn giữ nguyên (alias) cho ai đã quen gõ nó.
    if not argument.strip():
        return await reminders.list_reminders(user.id)
    spec, _, content = argument.partition(" ")
    return await reminders.add_reminder(user.id, spec, content)


async def _cmd_dsnhac(user: User, argument: str) -> str:
    return await reminders.list_reminders(user.id)


async def _cmd_xoanhac(user: User, argument: str) -> str:
    return await reminders.remove_reminder(user.id, argument.strip())


async def _cmd_muavao(user: User, argument: str) -> str:
    parts = argument.split()
    if len(parts) < 3:
        return "Cú pháp: /muavao <MÃ> <khối lượng> <giá mua>"
    return await portfolio.buy(user.id, parts[0], parts[1], parts[2])


async def _cmd_banra(user: User, argument: str) -> str:
    parts = argument.split()
    if len(parts) < 2:
        return "Cú pháp: /banra <MÃ> <khối lượng>"
    return await portfolio.sell(user.id, parts[0], parts[1])


async def _cmd_xoadanhmuc(user: User, argument: str) -> str:
    if not argument:
        return "Cú pháp: /xoadanhmuc <MÃ>"
    return await portfolio.remove(user.id, argument.split()[0])


async def _cmd_muctieu(user: User, argument: str) -> str:
    parts = argument.split()
    if len(parts) < 3:
        return "Cú pháp: /muctieu <MÃ> <giá stop hoặc -> <giá target hoặc ->"
    return await portfolio.set_alerts(user.id, parts[0], parts[1], parts[2])


async def _cmd_danhmuc(user: User, argument: str) -> str:
    return await portfolio.list_portfolio(user.id)


async def _cmd_rag(user: User, argument: str) -> str:
    choice = argument.strip().lower()
    if choice in {"on", "bat", "bật"}:
        await database.set_rag_enabled(user.id, True)
        return "✅ Đã bật tra cứu knowledge base. Câu hỏi liên quan tài liệu sẽ được tra cứu."
    if choice in {"off", "tat", "tắt"}:
        await database.set_rag_enabled(user.id, False)
        return "🚫 Đã tắt tra cứu knowledge base. Chat sẽ trả lời bình thường, không tốn quota tra cứu."
    status = "BẬT ✅" if user.rag_enabled else "TẮT 🚫"
    return f"Trạng thái RAG hiện tại: {status}\nCú pháp: /rag on | /rag off"


async def _cmd_nhomzalo(user: User, argument: str) -> str:
    if not user.can_use_group_summary:
        return _GROUP_FEATURE_DENIED
    return await zalo_groups.list_groups()


async def _cmd_themnhom(user: User, argument: str) -> str:
    if not user.can_use_group_summary:
        return _GROUP_FEATURE_DENIED
    group_id, _, alias = argument.strip().partition(" ")
    return await zalo_groups.add_group(group_id, alias.strip())


async def _cmd_xoanhom(user: User, argument: str) -> str:
    if not user.can_use_group_summary:
        return _GROUP_FEATURE_DENIED
    return await zalo_groups.remove_group(argument.strip())


async def _cmd_tongket(user: User, argument: str) -> str:
    # Cho phép admin (Zalo lẫn Zoom/Telegram, vì role là thuộc tính của user, không phải
    # kênh) hỏi "nhóm Zalo đang thảo luận gì" ngay từ Zoom/Telegram, không chỉ từ trong
    # group chat Zalo. summarize_group() tự kiểm tra can_use_group_summary bên trong.
    parts = argument.split()
    if not parts:
        return "Cú pháp: /tongket <nhóm> [24h|7d]"
    return await summarize_group(user, parts[0], parts[1] if len(parts) > 1 else "24h")


async def _cmd_dangnoi(user: User, argument: str) -> str:
    # Giống /tongket về nguyên tắc dùng chéo kênh, nhưng trả nguyên văn tin nhắn
    # trong ngày (giờ VN) thay vì bản tóm tắt AI - today_discussion() tự kiểm tra
    # can_use_group_summary bên trong.
    parts = argument.split()
    if not parts:
        return "Cú pháp: /dangnoi <nhóm> — xem nguyên văn thảo luận trong ngày hôm nay"
    return await today_discussion(user, parts[0])


async def _cmd_help(user: User, argument: str) -> str:
    lines = ["📖 Danh sách lệnh:", ""]
    lines.extend(entry.help for entry in COMMANDS.values())
    lines.append("")
    lines.append("Mẹo: gõ đúng 3 chữ cái (vd FPT) để tra nhanh giá, không cần gõ /quote.")
    return "\n".join(lines)


COMMANDS: dict[str, Command] = {
    "/gia": Command(_cmd_gia, "/gia <sản phẩm> — tra giá bán hiện tại"),
    "/prompt": Command(_cmd_prompt, "/prompt <mô tả ảnh> — viết prompt tạo ảnh"),
    "/stock": Command(_cmd_stock, "/stock <MÃ> [sâu] — phân tích cổ phiếu"),
    "/vimo": Command(_cmd_vimo, "/vimo <câu hỏi> — tin tức/vĩ mô thị trường"),
    "/dich": Command(
        _cmd_dich,
        "/dich [ja>vi|vi>ja] <nội dung> — dịch chat công việc Nhật-Việt (KIV), "
        "không chỉ định chiều thì tự nhận diện",
    ),
    "/quote": Command(_cmd_quote, "/quote <MÃ> — tra nhanh giá (hoặc gõ đúng 3 chữ cái)"),
    "/ghichu": Command(
        _cmd_ghichu, "/ghichu [nội dung] — có nội dung: lưu ghi chú; để trống: xem danh sách"
    ),
    "/dsghichu": Command(_cmd_dsghichu, "/dsghichu — xem danh sách ghi chú (giống /ghichu để trống)"),
    "/xoaghichu": Command(_cmd_xoaghichu, "/xoaghichu <id> — xoá ghi chú"),
    "/nhac": Command(
        _cmd_nhac,
        "/nhac [30p|2h|1ngay|HH:MM] [nội dung] — có nội dung: đặt nhắc nhở; để trống: xem danh sách",
    ),
    "/dsnhac": Command(_cmd_dsnhac, "/dsnhac — xem danh sách nhắc nhở (giống /nhac để trống)"),
    "/xoanhac": Command(_cmd_xoanhac, "/xoanhac <id> — xoá nhắc nhở"),
    "/muavao": Command(_cmd_muavao, "/muavao <MÃ> <KL> <giá> — ghi nhận mua vào danh mục"),
    "/banra": Command(_cmd_banra, "/banra <MÃ> <KL> — ghi nhận bán ra"),
    "/xoadanhmuc": Command(_cmd_xoadanhmuc, "/xoadanhmuc <MÃ> — xoá khỏi danh mục"),
    "/muctieu": Command(
        _cmd_muctieu, "/muctieu <MÃ> <giá stop hoặc -> <giá target hoặc -> — đặt mức tham khảo"
    ),
    "/danhmuc": Command(_cmd_danhmuc, "/danhmuc — xem danh mục"),
    "/rag": Command(_cmd_rag, "/rag [on|off] — bật/tắt tra cứu knowledge base cho chat"),
    "/nhom": Command(
        _cmd_nhomzalo,
        "/nhom — xem danh sách nhóm Zalo đã bật allowlist (chỉ admin)",
    ),
    "/nhomzalo": Command(
        _cmd_nhomzalo,
        "/nhomzalo — giống /nhom (xem danh sách nhóm Zalo, chỉ admin)",
    ),
    "/themnhom": Command(
        _cmd_themnhom, "/themnhom <group_id> [alias] — thêm nhóm Zalo vào allowlist (chỉ admin)"
    ),
    "/xoanhom": Command(
        _cmd_xoanhom, "/xoanhom <group_id hoặc alias> — gỡ nhóm Zalo khỏi allowlist (chỉ admin)"
    ),
    "/tongket": Command(
        _cmd_tongket,
        "/tongket <nhóm> [24h|7d] — nhóm Zalo đang thảo luận gì (tóm tắt AI, chỉ admin), "
        "dùng được cả từ Zoom/Telegram",
    ),
    "/dangnoi": Command(
        _cmd_dangnoi,
        "/dangnoi <nhóm> — xem nguyên văn thảo luận trong ngày hôm nay của nhóm Zalo "
        "(không qua AI tóm tắt, chỉ admin), dùng được cả từ Zoom/Telegram",
    ),
    "/help": Command(_cmd_help, "/help — xem danh sách lệnh"),
}


async def handle(user: User, text: str) -> str | None:
    command, _, argument = text.strip().partition(" ")
    entry = COMMANDS.get(command.lower())
    if entry is None:
        return None
    return await entry.handler(user, argument)
