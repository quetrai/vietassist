import json

from ai.contracts import AIResponse, ProviderError
from services import memory

_USER_ID = "u-mem-1"


async def test_build_memory_context_empty_when_no_facts(monkeypatch):
    async def fake_memory(user_id):
        return []

    monkeypatch.setattr(memory.database, "memory", fake_memory)

    assert await memory.build_memory_context(_USER_ID) == ""


async def test_build_memory_context_formats_facts_as_bullets(monkeypatch):
    async def fake_memory(user_id):
        return ["Tên là Nam", "Thích cổ phiếu ngân hàng"]

    monkeypatch.setattr(memory.database, "memory", fake_memory)

    context = await memory.build_memory_context(_USER_ID)

    assert "- Tên là Nam" in context
    assert "- Thích cổ phiếu ngân hàng" in context


async def test_build_memory_context_fails_safe_on_db_error(monkeypatch):
    async def fake_memory(user_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(memory.database, "memory", fake_memory)

    assert await memory.build_memory_context(_USER_ID) == ""


async def test_update_memory_saves_merged_facts(monkeypatch):
    captured = {}

    async def fake_memory(user_id):
        return ["Tên là Nam"]

    async def fake_save_memory(user_id, facts):
        captured["facts"] = facts

    async def fake_text(task_type, messages, *, system, temperature=0.5):
        return AIResponse(
            text=json.dumps({"facts": ["Tên là Nam", "Thích đầu tư dài hạn"]}),
            provider="test", model="test",
        )

    monkeypatch.setattr(memory.database, "memory", fake_memory)
    monkeypatch.setattr(memory.database, "save_memory", fake_save_memory)
    monkeypatch.setattr(memory.router, "text", fake_text)

    await memory.update_memory(_USER_ID, "Tôi thích đầu tư dài hạn", "Đã ghi nhớ nhé!")

    assert captured["facts"] == ["Tên là Nam", "Thích đầu tư dài hạn"]


async def test_update_memory_skips_write_when_facts_unchanged(monkeypatch):
    async def fake_memory(user_id):
        return ["Tên là Nam"]

    async def fail_save_memory(user_id, facts):
        raise AssertionError("không được ghi DB khi facts không đổi")

    async def fake_text(task_type, messages, *, system, temperature=0.5):
        return AIResponse(text=json.dumps({"facts": ["Tên là Nam"]}), provider="test", model="test")

    monkeypatch.setattr(memory.database, "memory", fake_memory)
    monkeypatch.setattr(memory.database, "save_memory", fail_save_memory)
    monkeypatch.setattr(memory.router, "text", fake_text)

    await memory.update_memory(_USER_ID, "chào", "chào bạn")


async def test_update_memory_swallows_provider_error(monkeypatch):
    async def fake_memory(user_id):
        return []

    async def fail_save_memory(user_id, facts):
        raise AssertionError("không được ghi DB khi trích xuất lỗi")

    async def fake_text(task_type, messages, *, system, temperature=0.5):
        raise ProviderError("timeout")

    monkeypatch.setattr(memory.database, "memory", fake_memory)
    monkeypatch.setattr(memory.database, "save_memory", fail_save_memory)
    monkeypatch.setattr(memory.router, "text", fake_text)

    await memory.update_memory(_USER_ID, "chào", "chào bạn")  # không raise


async def test_update_memory_swallows_invalid_json(monkeypatch):
    async def fake_memory(user_id):
        return []

    async def fail_save_memory(user_id, facts):
        raise AssertionError("không được ghi DB khi JSON không hợp lệ")

    async def fake_text(task_type, messages, *, system, temperature=0.5):
        return AIResponse(text="không phải JSON", provider="test", model="test")

    monkeypatch.setattr(memory.database, "memory", fake_memory)
    monkeypatch.setattr(memory.database, "save_memory", fail_save_memory)
    monkeypatch.setattr(memory.router, "text", fake_text)

    await memory.update_memory(_USER_ID, "chào", "chào bạn")  # không raise


def test_sanitize_facts_dedupes_case_insensitively():
    facts = memory._sanitize_facts(["Thích cổ phiếu", "thích cổ phiếu", "Thích ngân hàng"])
    assert facts == ["Thích cổ phiếu", "Thích ngân hàng"]


def test_sanitize_facts_caps_list_length():
    raw = [f"fact {i}" for i in range(50)]
    facts = memory._sanitize_facts(raw)
    assert len(facts) == memory.MAX_FACTS_PER_USER


def test_sanitize_facts_ignores_non_string_items():
    assert memory._sanitize_facts([123, None, "fact hợp lệ"]) == ["fact hợp lệ"]


def test_sanitize_facts_returns_empty_for_non_list():
    assert memory._sanitize_facts("not a list") == []


def test_strip_json_fence_removes_markdown_code_block():
    text = "```json\n{\"facts\": []}\n```"
    assert memory._strip_json_fence(text) == '{"facts": []}'
