"""Trí nhớ DÀI HẠN của bot — khác trí nhớ NGẮN HẠN theo phiên đã có sẵn
(`core.database.recent_messages`/`chat_messages`, chỉ giữ N lượt gần nhất).

Bảng `user_memory` (xem `core/database.py`) đã tồn tại từ trước (cột
`facts JSONB`) nhưng CHƯA TỪNG được ghi hay đọc ở đâu — module này là phần
còn thiếu, nối dây cho hạ tầng có sẵn đó hoạt động.

Khác với repo Gemini (2 bảng riêng: `user_facts` dạng key/value + 1 bảng tóm
tắt "rolling" riêng), ở đây CỐ Ý đơn giản hơn để khớp schema `user_memory`
sẵn có của vietassist: chỉ MỘT danh sách chuỗi fact ngắn gọn / user (không
tách bảng tóm tắt riêng) — mỗi lần cập nhật, LLM trả về TOÀN BỘ danh sách fact
sau khi đã hợp nhất (giữ fact cũ còn đúng, sửa fact đã lỗi thời, thêm fact
mới), không phải chỉ phần chênh lệch.

Luồng dùng (xem `services/chat.py`):
1. Trước khi gọi AI trả lời: `build_memory_context(user_id)` -> chèn vào
   system prompt.
2. Sau khi có phản hồi thành công: `update_memory(user_id, text, reply)` chạy
   NGẦM (`asyncio.create_task`, không await trong luồng trả lời) để không làm
   chậm phản hồi cho người dùng — và không bao giờ raise ra ngoài task đó
   (lỗi trích xuất fact không được làm crash hay log ồn ào task nền).
"""
from __future__ import annotations

import json
import logging

from ai import router
from ai.contracts import ProviderError, TaskType
from core import database
from services.locks import user_lock

logger = logging.getLogger(__name__)

# Trần số fact lưu / user — tránh user_memory.facts phình vô hạn qua thời gian
# nếu model trích xuất quá tay (vd nhớ nhầm chuyện phiếm thành "sự thật").
MAX_FACTS_PER_USER = 30
_MAX_FACT_LEN = 200

_EXTRACTION_SYSTEM = """Bạn là bộ trích xuất trí nhớ nội bộ cho 1 trợ lý cá nhân (KHÔNG phải người
đang trò chuyện trực tiếp với user). Đọc lượt hội thoại mới nhất (User nói
gì, Trợ lý trả lời gì) cùng danh sách "sự thật đã biết" hiện có, rồi trả về
DUY NHẤT 1 object JSON hợp lệ (không thêm chữ nào khác, không markdown, không
code fence, không giải thích) đúng định dạng:

{"facts": ["câu sự thật ngắn gọn 1", "câu sự thật ngắn gọn 2"]}

"facts" PHẢI là TOÀN BỘ danh sách sau khi đã hợp nhất (không phải chỉ phần
thêm mới): giữ nguyên các fact cũ còn đúng, SỬA fact đã bị user đính chính lại
(không giữ cả bản cũ lẫn bản mới), bỏ fact đã lỗi thời hẳn, thêm fact mới nếu
có. Nếu lượt hội thoại này không có gì đáng nhớ và không có gì cần sửa, trả về
đúng nguyên danh sách fact cũ.

Quy tắc bắt buộc:
- CHỈ giữ sự thật BỀN VỮNG về người dùng: tên, cách xưng hô, sở thích, mã cổ
  phiếu/lĩnh vực quan tâm, công việc, thói quen, mục tiêu dài hạn... KHÔNG ghi
  chuyện phiếm nhất thời, thời tiết, cảm xúc thoáng qua, câu hỏi một lần.
- Mỗi fact là 1 câu ngắn gọn (dưới 25 từ), tiếng Việt, ngôi thứ 3 (vd "Thích
  đầu tư dài hạn vào ngành ngân hàng", không phải "Tôi thích...").
- Tối đa 30 fact — nếu vượt, bỏ bớt fact ít quan trọng/ít liên quan nhất.
- Tuyệt đối KHÔNG bịa thêm thông tin không có trong hội thoại được cung cấp."""


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()


def _sanitize_facts(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    facts: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        fact = item.strip()[:_MAX_FACT_LEN]
        if not fact or fact.lower() in seen:
            continue
        seen.add(fact.lower())
        facts.append(fact)
        if len(facts) >= MAX_FACTS_PER_USER:
            break
    return facts


async def build_memory_context(user_id: str) -> str:
    """Trả về khối text chèn vào system prompt, hoặc "" nếu chưa có fact nào
    (system prompt không nên có 1 đoạn "chưa biết gì" vô nghĩa)."""
    try:
        facts = await database.memory(user_id)
    except Exception:
        logger.warning("build_memory_context lỗi cho user %s", user_id, exc_info=True)
        return ""
    if not facts:
        return ""
    bullet_list = "\n".join(f"- {fact}" for fact in facts)
    return (
        "Thông tin đã biết về người dùng từ các lần trò chuyện trước (dùng khi liên quan, "
        "không lặp lại y nguyên như đọc báo cáo, không bịa thêm ngoài danh sách này):\n"
        f"{bullet_list}"
    )


async def update_memory(user_id: str, user_text: str, assistant_text: str) -> None:
    """Trích xuất fact mới/cập nhật từ 1 lượt hội thoại, hợp nhất với fact cũ,
    rồi lưu lại. KHÔNG BAO GIỜ raise — gọi qua asyncio.create_task() nên lỗi ở
    đây không được phép làm crash task nền hay ảnh hưởng phản hồi đã gửi."""
    lock = await user_lock(f"memory:{user_id}")
    async with lock:
        try:
            existing = await database.memory(user_id)
            facts_block = "\n".join(f"- {fact}" for fact in existing) if existing else "(chưa có)"
            prompt = (
                f"Sự thật đã biết:\n{facts_block}\n\n"
                f"Lượt hội thoại mới nhất:\nUser: {user_text}\nTrợ lý: {assistant_text}"
            )
            response = await router.text(
                TaskType.CHAT,
                [{"role": "user", "content": prompt}],
                system=_EXTRACTION_SYSTEM,
                temperature=0.2,
            )
            parsed = json.loads(_strip_json_fence(response.text))
            facts = _sanitize_facts(parsed.get("facts"))
        except (ProviderError, json.JSONDecodeError, AttributeError, TypeError):
            logger.info("update_memory: bỏ qua lượt này cho user %s (lỗi trích xuất)", user_id)
            return
        except Exception:
            logger.warning("update_memory lỗi không mong đợi cho user %s", user_id, exc_info=True)
            return
        if facts == list(existing):
            return  # không đổi gì, tránh ghi DB vô ích
        try:
            await database.save_memory(user_id, facts)
        except Exception:
            logger.warning("update_memory: lưu DB lỗi cho user %s", user_id, exc_info=True)
