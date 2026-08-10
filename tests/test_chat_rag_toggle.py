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

    async def fake_system_with_knowledge(query, *, rag_enabled):
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

    monkeypatch.setattr(chat, "_system_with_knowledge", fake_system_with_knowledge)
    monkeypatch.setattr(chat.database, "history", fake_history)
    monkeypatch.setattr(chat.database, "add_message", fake_add_message)
    monkeypatch.setattr(chat.router, "text", fake_text)

    user_off = User("u1", Channel.TELEGRAM, "1", Role.ROOT, True, False)
    await chat.chat(user_off, "xin chào")
    assert captured["rag_enabled"] is False

    user_on = User("u1", Channel.TELEGRAM, "1", Role.ROOT, True, True)
    await chat.chat(user_on, "xin chào")
    assert captured["rag_enabled"] is True
