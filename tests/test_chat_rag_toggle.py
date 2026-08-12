import services.chat as chat
from core.models import Channel, Role, User


async def test_system_with_knowledge_skips_retrieve_when_rag_disabled(monkeypatch):
    """Khi user tắt RAG (/rag off hoặc nút bấm), chat() không được gọi knowledge.retrieve()
    dù knowledge base có nội dung — đây là điểm tiết kiệm quota chính."""

    async def fail_retrieve(query):
        raise AssertionError("knowledge.retrieve() không được gọi khi rag_enabled=False")

    monkeypatch.setattr(chat.knowledge, "retrieve", fail_retrieve)
    system = await chat._system_with_knowledge("câu hỏi bất kỳ", rag_enabled=False)
    assert system == chat.SYSTEM_PROMPT


async def test_system_with_knowledge_calls_retrieve_when_rag_enabled(monkeypatch):
    captured = {}

    async def fake_retrieve(query):
        captured["query"] = query
        return "[nguon.md]\nnội dung liên quan"

    monkeypatch.setattr(chat.knowledge, "retrieve", fake_retrieve)
    system = await chat._system_with_knowledge("câu hỏi cần tra cứu", rag_enabled=True)
    assert captured["query"] == "câu hỏi cần tra cứu"
    assert "nội dung liên quan" in system
    assert chat.SYSTEM_PROMPT in system


async def test_chat_passes_user_rag_flag(monkeypatch):
    """chat() phải truyền đúng user.rag_enabled xuống _system_with_knowledge, không phải
    hardcode True — regression cho lỗi quên đọc cờ khi thêm tính năng mới."""
    captured = {}

    async def fake_system_with_knowledge(query, *, rag_enabled, memory_context=""):
        captured["rag_enabled"] = rag_enabled
        return "system"

    async def fake_history(user_id, turns):
        return []

    async def fake_add_message(user_id, role, content):
        return None

    class FakeResponse:
        text = "trả lời"
        provider = "fake"

    async def fake_text(task_type, messages, system=None):
        return FakeResponse()

    async def fake_update_memory(user_id, user_text, assistant_text):
        return None

    monkeypatch.setattr(chat, "_system_with_knowledge", fake_system_with_knowledge)
    monkeypatch.setattr(chat.database, "history", fake_history)
    monkeypatch.setattr(chat.database, "add_message", fake_add_message)
    monkeypatch.setattr(chat.router, "text", fake_text)
    monkeypatch.setattr(chat.memory, "update_memory", fake_update_memory)

    user_off = User("u1", Channel.TELEGRAM, "1", Role.ROOT, True, False)
    await chat.chat(user_off, "xin chào")
    assert captured["rag_enabled"] is False

    user_on = User("u1", Channel.TELEGRAM, "1", Role.ROOT, True, True)
    await chat.chat(user_on, "xin chào")
    assert captured["rag_enabled"] is True


async def test_chat_returns_tool_reply_without_calling_llm_chat(monkeypatch):
    """Khi intent_router khớp 1 tool (vd ghi chú qua chat tự do), chat() phải trả
    thẳng kết quả tool, KHÔNG gọi router.text (chat LLM) hay ghi vào chat_messages —
    xem services/chat.py, tránh làm nhiễu lịch sử hội thoại bằng output kiểu lệnh."""

    async def fake_maybe_run_tool(user, text):
        return "Đã ghi chú: \"mua sữa\""

    async def fail_history(user_id, turns):
        raise AssertionError("không được gọi database.history khi đã khớp tool")

    async def fail_add_message(user_id, role, content):
        raise AssertionError("không được ghi chat_messages cho tool reply")

    async def fail_text(task_type, messages, system=None):
        raise AssertionError("không được gọi router.text khi đã khớp tool")

    monkeypatch.setattr(chat, "maybe_run_tool", fake_maybe_run_tool)
    monkeypatch.setattr(chat.database, "history", fail_history)
    monkeypatch.setattr(chat.database, "add_message", fail_add_message)
    monkeypatch.setattr(chat.router, "text", fail_text)

    user = User("u1", Channel.TELEGRAM, "1", Role.ROOT, True, False)
    result, provider = await chat.chat(user, "ghi chú giúp anh mua sữa")

    assert result == 'Đã ghi chú: "mua sữa"'
    assert provider == "tool"


async def test_chat_falls_through_to_normal_chat_when_no_tool_matched(monkeypatch):
    async def fake_maybe_run_tool(user, text):
        return None

    async def fake_history(user_id, turns):
        return []

    async def fake_add_message(user_id, role, content):
        return None

    class FakeResponse:
        text = "trả lời bình thường"
        provider = "fake"

    async def fake_text(task_type, messages, system=None):
        return FakeResponse()

    async def fake_update_memory(user_id, user_text, assistant_text):
        return None

    async def fake_build_memory_context(user_id):
        return ""

    monkeypatch.setattr(chat, "maybe_run_tool", fake_maybe_run_tool)
    monkeypatch.setattr(chat.database, "history", fake_history)
    monkeypatch.setattr(chat.database, "add_message", fake_add_message)
    monkeypatch.setattr(chat.router, "text", fake_text)
    monkeypatch.setattr(chat.memory, "update_memory", fake_update_memory)
    monkeypatch.setattr(chat.memory, "build_memory_context", fake_build_memory_context)

    user = User("u1", Channel.TELEGRAM, "1", Role.ROOT, True, False)
    result, provider = await chat.chat(user, "xin chào")

    assert result == "trả lời bình thường"
    assert provider == "fake"
