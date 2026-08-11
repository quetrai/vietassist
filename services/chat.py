from __future__ import annotations

from ai import router
from ai.contracts import ProviderError, TaskType
from core import database, knowledge
from core.config import settings
from core.models import User
from services.locks import user_lock
from services.prompt_engine import build_text_prompt_instruction

SYSTEM_PROMPT = """Bạn là VietAssist, trợ lý AI tiếng Việt rõ ràng và thực tế.
Không bịa dữ liệu hiện hành. Nếu thiếu dữ liệu, nói rõ giới hạn.
Tôn trọng riêng tư: không suy đoán hay tiết lộ dữ liệu của người dùng khác.
Trả lời ngắn gọn trước, bổ sung chi tiết khi cần."""

_REALTIME_MARKERS = (
    "tin tức hôm nay",
    "tin tức mới nhất",
    "tin mới nhất",
    "tin tức mới",
    "tin hôm nay",
    "bản tin hôm nay",
    "tin trong ngày",
    "tin mới",
    "cập nhật tin tức",
    "latest news",
    "news today",
    "giá hiện tại",
    "giá hôm nay",
    "giá mới nhất",
    "giá bao nhiêu hiện tại",
    "hiện tại bao nhiêu",
    "hôm nay bao nhiêu",
    "tỷ giá hôm nay",
    "tỷ giá hiện tại",
    "giá vàng hôm nay",
    "giá vàng hiện tại",
    "bitcoin hôm nay",
    "btc hôm nay",
    "thời tiết hôm nay",
    "thời tiết hiện tại",
    "bây giờ",
    "hiện giờ",
    "mới nhất",
    "cập nhật mới nhất",
    "latest",
    "today",
    "right now",
    "currently",
)


def _is_realtime_request(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    if any(marker in normalized for marker in _REALTIME_MARKERS):
        return True

    # Natural-language product/market price queries without explicit "hôm nay".
    price_words = ("giá ", "giá của ", "bao nhiêu tiền", "giá bán")
    product_words = ("samsung", "iphone", "xiaomi", "oppo", "laptop", "điện thoại", "macbook")
    return any(w in normalized for w in price_words) and any(w in normalized for w in product_words)


async def _system_with_knowledge(query: str, *, rag_enabled: bool) -> str:
    if not rag_enabled:
        return SYSTEM_PROMPT
    context = await knowledge.retrieve(query)
    if not context:
        return SYSTEM_PROMPT
    return (
        f"{SYSTEM_PROMPT}\n\n"
        "Dữ liệu tham khảo liên quan nhất tới câu hỏi (trích từ knowledge base do người vận "
        "hành cung cấp) — ưu tiên dùng khi liên quan, không bịa thêm ngoài phạm vi này:\n" + context
    )


async def chat(user: User, text: str) -> tuple[str, str]:
    if not user.active:
        return "Tài khoản đang bị tạm khóa.", "system"

    lock = await user_lock(user.id)
    async with lock:
        # Mọi yêu cầu cần dữ liệu hiện hành đều phải qua realtime web search.
        # Không fallback sang LLM chat thường vì đó có thể là dữ liệu cũ.
        if _is_realtime_request(text):
            try:
                response = await router.macro_news(text)
            except ProviderError:
                response = await router.product_search(text) if any(
                    word in text.casefold() for word in ("giá ", "giá của ", "giá bán", "bao nhiêu tiền")
                ) else None
                if response is None:
                    return (
                        "Không tìm thấy dữ liệu thời gian thực đã được xác minh lúc này. "
                        "Tôi không có đủ kết quả web để trả lời mà không bịa thông tin.",
                        "realtime-unavailable",
                    )
        else:
            history = await database.history(user.id, settings.chat_history_turns)
            messages = [*history, {"role": "user", "content": text}]
            system = await _system_with_knowledge(text, rag_enabled=user.rag_enabled)
            response = await router.text(TaskType.CHAT, messages, system=system)

        await database.add_message(user.id, "user", text)
        await database.add_message(user.id, "assistant", response.text)
        return response.text, response.provider


async def generate_text_prompt(request: str) -> tuple[str, str, str]:
    instruction, spec = build_text_prompt_instruction(request)
    response = await router.text(
        TaskType.TEXT_PROMPT,
        [{"role": "user", "content": instruction}],
        system="You are a strict image prompt generation service. Follow the supplied prompt-engineering rules.",
        temperature=0.65,
    )
    return response.text.strip(), response.provider, spec.hint


async def text_to_prompt(user: User, request: str) -> tuple[str, str]:
    result, provider, _ = await generate_text_prompt(request)
    return result, provider
