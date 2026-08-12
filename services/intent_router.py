"""Định tuyến ý định (intent router) bằng ngôn ngữ tự nhiên — port RÚT GỌN/thích nghi
từ repo Gemini (`services/tools.py`).

Cho phép gõ tự do kiểu "ghi chú giúp anh mua sữa" hoặc "8 giờ tối nhắc anh gọi mẹ"
trong chat bình thường (không cần gõ đúng `/ghichu`, `/nhac`) — hỏi 1 lượt LLM xem
tin nhắn có khớp Ý ĐỊNH ghi chú/nhắc nhở RÕ RÀNG không, nếu có thì tự thực thi qua
đúng `services/reminders.py` (không viết lại logic lưu note/reminder ở đây).

Khác với repo Gemini:
- Gemini bắt buộc dùng `official_client.generate_utility_json()` (bỏ qua hẳn
  provider-chain, gọi thẳng Google AI Studio) vì nhánh cookie của họ không hỗ trợ
  JSON đáng tin cậy. vietassist không có provider cookie nào (đã bỏ từ đầu — xem
  README), nên ở đây dùng thẳng `ai.router.text()` — cùng chuỗi fallback
  Groq → OpenRouter → Gemini mà chat chính đang dùng, không cần đường riêng.
- KHÔNG có tool "get_portfolio": `/danhmuc` đã đủ nhanh gõ, và để LLM tự "nhớ" danh
  mục qua router này dễ trả lời sai lệch với dữ liệu thật trong `stock_holdings`
  (nguồn sự thật duy nhất phải là DB, không phải suy luận của model).
- CHỦ Ý KHÔNG đụng vào đường lấy giá cổ phiếu (`commands.try_ticker_quote`,
  `stock.analysis`): đó là fast-path deterministic lấy giá REALTIME thật từ DNSE,
  không phụ thuộc 1 lượt gọi LLM có thể lỗi/trả sai định dạng — đổi sang router này
  sẽ đánh đổi độ tin cậy của tính năng đã chạy tốt để lấy sự "gọn" không cần thiết.

Đánh đổi: thêm 1 lượt gọi LLM phụ / tin nhắn CHAT TỰ DO (không áp dụng cho lệnh
`/...` — những lệnh đó đã tường minh, không cần đoán ý định). Chấp nhận được vì
đây là bot cá nhân/nhóm nhỏ, tần suất thấp; và nếu lượt gọi này lỗi/timeout,
`maybe_run_tool()` trả None êm — chat chính rơi xuống flow bình thường, không bị
chặn hay lỗi hiển thị ra người dùng.
"""
from __future__ import annotations

import json
import logging

from ai import router
from ai.contracts import ProviderError, TaskType
from core.models import User
from services import reminders

logger = logging.getLogger(__name__)

_ROUTER_SYSTEM = """Bạn là bộ định tuyến tool (function router) nội bộ cho 1 trợ lý cá nhân.
Đọc tin nhắn của người dùng và quyết định có cần gọi 1 trong các tool sau không.
Trả về DUY NHẤT 1 object JSON hợp lệ (không markdown, không code fence, không thêm
chữ nào khác) đúng định dạng:

{"tool": "ten_tool_hoac_none", "args": {...}}

Danh sách tool khả dụng:
- "save_note": lưu 1 ghi chú tự do. args: {"content": "nội dung cần ghi nhớ"}. Dùng
  khi người dùng RÕ RÀNG yêu cầu ghi/ghi chú/note lại điều gì đó (vd "ghi chú giúp
  anh...", "note lại...", "nhớ giúp anh là...").
- "list_notes": xem lại các ghi chú đã lưu. args: {} (rỗng). Dùng khi người dùng hỏi
  "em ghi chú gì rồi", "xem lại note của anh"...
- "set_reminder": đặt nhắc việc. args: {"spec": "...", "content": "nội dung cần
  nhắc"}. "spec" PHẢI đúng 1 trong 2 dạng: số+đơn vị viết liền không dấu cách (vd
  "30p" = 30 phút nữa, "2h" = 2 giờ nữa, "1ngay" = 1 ngày nữa), HOẶC giờ tuyệt đối
  "HH:MM" 24h theo giờ Việt Nam (vd "20:00"). Dùng khi người dùng RÕ RÀNG yêu cầu
  nhắc việc vào 1 thời điểm hoặc sau 1 khoảng thời gian (vd "nhắc anh 30 phút nữa
  uống thuốc", "8 giờ tối nhắc anh gọi điện cho mẹ" -> spec "20:00").
- "list_reminders": xem lại các nhắc nhở đang chờ. args: {} (rỗng).
- "none": tin nhắn KHÔNG cần gọi tool nào (chuyện phiếm, hỏi đáp thông thường, hỏi
  giá/phân tích cổ phiếu, tin tức - các trường hợp đó đã được xử lý riêng, không
  thuộc phạm vi router này). args: {}.

Quy tắc bắt buộc:
- CHỈ chọn 1 tool khi người dùng có ý định RÕ RÀNG khớp mô tả trên. Nếu không chắc
  hoặc chỉ nhắc tới liên quan mơ hồ, chọn "none".
- KHÔNG tự bịa nội dung cho "content" - lấy đúng ý người dùng đã nói, có thể viết
  lại ngắn gọn hơn nhưng không thêm thông tin mới."""


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()


async def _route(text: str) -> dict[str, object] | None:
    try:
        response = await router.text(
            TaskType.CHAT,
            [{"role": "user", "content": text}],
            system=_ROUTER_SYSTEM,
            temperature=0.0,
        )
        parsed = json.loads(_strip_json_fence(response.text))
    except (ProviderError, json.JSONDecodeError, AttributeError, TypeError):
        return None
    except Exception:
        logger.warning("intent_router._route lỗi không mong đợi", exc_info=True)
        return None
    return parsed if isinstance(parsed, dict) else None


async def maybe_run_tool(user: User, text: str) -> str | None:
    """Trả về câu trả lời nếu tin nhắn khớp 1 tool (save_note/list_notes/set_reminder/
    list_reminders), hoặc None nếu không khớp/không xác định được — None nghĩa là
    "rơi xuống chat bình thường", KHÔNG phải lỗi. Không bao giờ raise."""
    parsed = await _route(text)
    if not parsed:
        return None
    tool = parsed.get("tool")
    args = parsed.get("args") if isinstance(parsed.get("args"), dict) else {}

    if tool == "save_note":
        content = str(args.get("content", "")).strip()
        if not content:
            return None
        return await reminders.add_note(user.id, content)

    if tool == "list_notes":
        return await reminders.list_notes(user.id)

    if tool == "set_reminder":
        spec = str(args.get("spec", "")).strip()
        content = str(args.get("content", "")).strip()
        if not spec:
            return None
        return await reminders.add_reminder(user.id, spec, content)

    if tool == "list_reminders":
        return await reminders.list_reminders(user.id)

    return None
