from __future__ import annotations

import asyncio
from pathlib import Path

import jinja2
import yaml

from ai import router
from ai.contracts import ProviderError, TaskType
from core import database, knowledge
from core.config import settings
from core.models import User
from services import memory
from services.intent_router import maybe_run_tool
from services.locks import user_lock
from services.prompt_engine import build_text_prompt_instruction

# Giọng "Lan Anh" - persona lấy nguyên (chat_skill.yaml + templates/chat_skill_prompt.j2)
# từ dự án Gemini để dùng chung style trò chuyện. Vì ai/router.py truyền cùng một
# `system` string này cho bất kỳ provider nào được chọn (Groq/OpenRouter/Google/9Router),
# nên đổi ở đây là đủ để thống nhất giọng trên mọi model, không cần sửa router.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PERSONA_YAML_PATH = _PROJECT_ROOT / "chat_skill.yaml"
_PERSONA_TEMPLATE_PATH = _PROJECT_ROOT / "templates" / "chat_skill_prompt.j2"

_persona_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(_PERSONA_TEMPLATE_PATH.parent),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _render_persona() -> str:
    data = yaml.safe_load(_PERSONA_YAML_PATH.read_text(encoding="utf-8"))
    template = _persona_env.get_template(_PERSONA_TEMPLATE_PATH.name)
    return template.render(
        p=data["persona"],
        tv=data["tone_of_voice"],
        rules=data["rules"],
        cm=data["content_modes"],
    ).strip()


SYSTEM_PROMPT = (
    _render_persona()
    + "\n\nNgoài phong cách trên, LUÔN tuân thủ thêm các nguyên tắc vận hành sau:\n"
    "- Không bịa dữ liệu hiện hành. Nếu thiếu dữ liệu, nói rõ giới hạn.\n"
    "- Tôn trọng riêng tư: không suy đoán hay tiết lộ dữ liệu của người dùng khác."
)

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


_NEWS_TOPIC_WORDS = ("tin tức", "bản tin", "tin nóng", "tin trong ngày", "news")
_FRESHNESS_WORDS = (
    "hôm nay",
    "hiện tại",
    "hiện giờ",
    "bây giờ",
    "mới nhất",
    "gần đây",
    "vừa qua",
    "trong ngày",
    "ngày hôm nay",
    "today",
    "latest",
    "now",
    "currently",
)
_LOOKUP_INTENT_WORDS = (
    "tra cứu",
    "tra cứu giúp",
    "tìm kiếm",
    "tìm giúp",
    "tìm hộ",
    "tìm thông tin",
    "kiểm tra giúp",
    "search",
    "look up",
    "lookup",
)


def _is_realtime_request(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    if any(marker in normalized for marker in _REALTIME_MARKERS):
        return True
    # Yêu cầu "tra cứu"/"tìm kiếm" tường minh -> LUÔN ép qua tìm kiếm thời gian
    # thực, tuyệt đối không rơi vào nhánh chat + knowledge base (yêu cầu của người
    # vận hành: KB chỉ được dùng khi không phải ý định tra cứu/tìm kiếm).
    if any(w in normalized for w in _LOOKUP_INTENT_WORDS):
        return True
    # Regression: "tin tức hôm nay" khớp _REALTIME_MARKERS vì đó là cụm liền nhau,
    # nhưng câu tự nhiên hay chèn thêm từ ở giữa (vd "tin tức về AI hôm nay",
    # "tin tức về thị trường chứng khoán hôm nay") thì so khớp cụm cố định bị lọt.
    # Bắt thêm bằng cách kết hợp: có từ khóa chủ đề tin tức + có mốc thời gian bất kỳ
    # đâu trong câu, không cần đứng liền kề nhau.
    has_news_topic = any(w in normalized for w in _NEWS_TOPIC_WORDS)
    has_freshness = any(w in normalized for w in _FRESHNESS_WORDS)
    if has_news_topic and has_freshness:
        return True

    # Natural-language product/market price queries without explicit "hôm nay".
    price_words = ("giá ", "giá của ", "bao nhiêu tiền", "giá bán")
    product_words = ("samsung", "iphone", "xiaomi", "oppo", "laptop", "điện thoại", "macbook")
    return any(w in normalized for w in price_words) and any(w in normalized for w in product_words)


async def _system_with_knowledge(query: str, *, rag_enabled: bool, memory_context: str = "") -> str:
    system = SYSTEM_PROMPT
    if memory_context:
        system = f"{system}\n\n{memory_context}"
    if not rag_enabled:
        return system
    context = await knowledge.retrieve(query)
    if not context:
        return system
    return (
        f"{system}\n\n"
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
            # Định tuyến ý định ghi chú/nhắc nhở bằng ngôn ngữ tự nhiên (vd "ghi chú
            # giúp anh...", "8 giờ tối nhắc anh gọi mẹ") — xem services/intent_router.py.
            # KHÔNG áp dụng cho nhánh realtime ở trên (không có ý nghĩa) hay các lệnh
            # "/..." (đã tường minh, xử lý riêng ở services/commands.py). Trả None êm
            # nếu không khớp/lỗi -> rơi xuống chat bình thường ngay dưới đây.
            tool_reply = await maybe_run_tool(user, text)
            if tool_reply is not None:
                return tool_reply, "tool"

            history = await database.history(user.id, settings.chat_history_turns)
            messages = [*history, {"role": "user", "content": text}]
            memory_context = await memory.build_memory_context(user.id)
            system = await _system_with_knowledge(text, rag_enabled=user.rag_enabled, memory_context=memory_context)
            response = await router.text(
                TaskType.CHAT, messages, system=system, prefer_router9=user.ai_router_enabled
            )

        await database.add_message(user.id, "user", text)
        await database.add_message(user.id, "assistant", response.text)
        # Trích xuất trí nhớ dài hạn chạy NGẦM, không await trong luồng trả lời cho
        # người dùng — xem services/memory.py. Không bao giờ raise, an toàn để "fire
        # and forget"; lỗi provider/JSON chỉ bị bỏ qua lượt đó, không ảnh hưởng chat.
        asyncio.create_task(memory.update_memory(user.id, text, response.text))
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
