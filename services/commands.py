from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ai import router
from ai.contracts import GroundingUnavailable, ProviderError
from core import database
from core.models import User
from services import portfolio, reminders
from services.chat import text_to_prompt
from services.prompt_engine import prompt_spec
from stock import analyze_symbol, quick_quote
from stock.analysis import normalize_symbol

Handler = Callable[[User, str], Awaitable[str]]


@dataclass(frozen=True)
class Command:
    handler: Handler
    help: str  # 1 dòng mô tả, dùng để sinh text /start — xem bot.py


async def try_ticker_quote(text: str) -> str | None:
    """Gõ đúng 3 chữ cái (không kèm lệnh) được hiểu là tra nhanh giá, kiểu /quote rút gọn.
    Trả None nếu không phải mã hợp lệ hoặc tra lỗi (mã không tồn tại, mạng lỗi tạm thời...)
    — để caller rơi về chat bình thường thay vì hiện lỗi khó hiểu cho 1 câu chat ngẫu nhiên
    tình cờ có đúng 3 chữ cái (vd "cho", "khi")."""
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
        return "Chưa cấu hình Google Search hoặc chưa xác minh được giá."
    except ProviderError:
        return "Không tra được giá lúc này (lỗi tạm thời), vui lòng thử lại sau."


async def _cmd_prompt(user: User, argument: str) -> str:
    if not argument:
        return "Cú pháp: /prompt <mô tả ảnh cần tạo>"
    try:
        result = (await text_to_prompt(user, argument))[0].strip()
        if not result:
            return "AI không trả về prompt, thử lại sau."
        return f"📝 Prompt gợi ý\\n{prompt_spec(argument).hint}\\n\\n{result}"
    except ProviderError:
        return "Không tạo được prompt lúc này (provider AI đang lỗi hoặc hết hạn mức), thử lại sau."


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
        return "Chưa cấu hình Google Search hoặc chưa xác minh được thông tin."
    except ProviderError:
        return "Không tra được thông tin lúc này (lỗi tạm thời), vui lòng thử lại sau."


async def _cmd_quote(user: User, argument: str) -> str:
    if not argument:
        return "Cú pháp: /quote <MÃ>"
    try:
        return await quick_quote(argument.split()[0])
    except (ValueError, RuntimeError) as exc:
        return f"Không lấy được giá: {exc}"


async def _cmd_ghichu(user: User, argument: str) -> str:
    return await reminders.add_note(user.id, argument)


async def _cmd_dsghichu(user: User, argument: str) -> str:
    return await reminders.list_notes(user.id)


async def _cmd_xoaghichu(user: User, argument: str) -> str:
    return await reminders.remove_note(user.id, argument.strip())


async def _cmd_nhac(user: User, argument: str) -> str:
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


async def _cmd_danhmuc(user: User, argument: str) -> str:
    return await portfolio.list_portfolio(user.id)


async def _cmd_rag(user: User, argument: str) -> str:
    """Bật/tắt tra cứu knowledge base (RAG) cho user. Không tham số -> xem trạng thái hiện
    tại kèm nút bấm nhanh (nút hiển thị ở bot.py, phần command này chỉ trả text thuần vì
    services/ không phụ thuộc thư viện Telegram)."""
    choice = argument.strip().lower()
    if choice in {"on", "bat", "bật"}:
        await database.set_rag_enabled(user.id, True)
        return "✅ Đã bật tra cứu knowledge base. Câu hỏi liên quan tài liệu sẽ được tra cứu."
    if choice in {"off", "tat", "tắt"}:
        await database.set_rag_enabled(user.id, False)
        return "🚫 Đã tắt tra cứu knowledge base. Chat sẽ trả lời bình thường, không tốn quota tra cứu."
    status = "BẬT ✅" if user.rag_enabled else "TẮT 🚫"
    return f"Trạng thái RAG hiện tại: {status}\nCú pháp: /rag on | /rag off"


# Nguồn sự thật DUY NHẤT cho các lệnh "text vào - text ra" chung. bot.py dùng chính dict
# này để đăng ký CommandHandler VÀ để sinh text hướng dẫn trong /start — thêm 1 lệnh mới
# chỉ cần thêm đúng 1 dòng ở đây, không phải sửa nhiều chỗ và không có gì nhắc nếu quên.
# (Các lệnh có hành vi đặc biệt — /start, /zalologin, /kbreindex, /zalopair...,
# /zalodanhsach — không theo khuôn (user, argument) -> str nên đăng ký riêng trong bot.py.)
COMMANDS: dict[str, Command] = {
    "/gia": Command(_cmd_gia, "/gia <sản phẩm> — tra giá bán hiện tại"),
    "/prompt": Command(_cmd_prompt, "/prompt <mô tả ảnh> — viết prompt tạo ảnh"),
    "/stock": Command(_cmd_stock, "/stock <MÃ> [sâu] — phân tích cổ phiếu"),
    "/vimo": Command(_cmd_vimo, "/vimo <câu hỏi> — tin tức/vĩ mô thị trường"),
    "/quote": Command(_cmd_quote, "/quote <MÃ> — tra nhanh giá (hoặc gõ đúng 3 chữ cái)"),
    "/ghichu": Command(_cmd_ghichu, "/ghichu <nội dung> — lưu ghi chú"),
    "/dsghichu": Command(_cmd_dsghichu, "/dsghichu — danh sách ghi chú"),
    "/xoaghichu": Command(_cmd_xoaghichu, "/xoaghichu <id> — xoá ghi chú"),
    "/nhac": Command(_cmd_nhac, "/nhac <30p|2h|1ngay|HH:MM> <nội dung> — đặt nhắc nhở"),
    "/dsnhac": Command(_cmd_dsnhac, "/dsnhac — danh sách nhắc nhở"),
    "/xoanhac": Command(_cmd_xoanhac, "/xoanhac <id> — xoá nhắc nhở"),
    "/muavao": Command(_cmd_muavao, "/muavao <MÃ> <KL> <giá> — ghi nhận mua vào danh mục"),
    "/banra": Command(_cmd_banra, "/banra <MÃ> <KL> — ghi nhận bán ra"),
    "/xoadanhmuc": Command(_cmd_xoadanhmuc, "/xoadanhmuc <MÃ> — xoá khỏi danh mục"),
    "/danhmuc": Command(_cmd_danhmuc, "/danhmuc — xem danh mục"),
    "/rag": Command(_cmd_rag, "/rag [on|off] — bật/tắt tra cứu knowledge base cho chat"),
}


async def handle(user: User, text: str) -> str | None:
    command, _, argument = text.strip().partition(" ")
    entry = COMMANDS.get(command.lower())
    if entry is None:
        return None
    return await entry.handler(user, argument)
