from __future__ import annotations

from ai import router
from ai.contracts import TaskType
from core import database, knowledge
from core.config import settings
from core.models import User
from services.locks import user_lock
from services.prompt_engine import build_text_prompt_instruction

SYSTEM_PROMPT = """Bạn là VietAssist, trợ lý AI tiếng Việt rõ ràng và thực tế.
Không bịa dữ liệu hiện hành. Nếu thiếu dữ liệu, nói rõ giới hạn.
Tôn trọng riêng tư: không suy đoán hay tiết lộ dữ liệu của người dùng khác.
Trả lời ngắn gọn trước, bổ sung chi tiết khi cần."""


async def _system_with_knowledge(query: str, *, rag_enabled: bool) -> str:
    # rag_enabled=False: user đã tự tắt RAG (lệnh /rag off hoặc nút bấm) vì chỉ cần chat
    # phiếm — bỏ hẳn bước gọi knowledge.retrieve() để không tốn 1 lượt Google Embedding
    # API cho mỗi tin nhắn không liên quan tới knowledge base.
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
