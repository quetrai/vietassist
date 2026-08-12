import json

from ai.contracts import AIResponse, ProviderError
from core.models import Channel, Role, User
from services import intent_router

_USER = User("u1", Channel.TELEGRAM, "1", Role.ROOT)


async def test_maybe_run_tool_returns_none_when_router_says_none(monkeypatch):
    async def fake_text(task_type, messages, *, system, temperature=0.5):
        return AIResponse(text=json.dumps({"tool": "none", "args": {}}), provider="t", model="t")

    monkeypatch.setattr(intent_router.router, "text", fake_text)

    assert await intent_router.maybe_run_tool(_USER, "trời hôm nay đẹp quá") is None


async def test_maybe_run_tool_save_note(monkeypatch):
    captured = {}

    async def fake_text(task_type, messages, *, system, temperature=0.5):
        return AIResponse(
            text=json.dumps({"tool": "save_note", "args": {"content": "mua sữa"}}),
            provider="t", model="t",
        )

    async def fake_add_note(user_id, content):
        captured["content"] = content
        return f'Đã ghi chú: "{content}"'

    monkeypatch.setattr(intent_router.router, "text", fake_text)
    monkeypatch.setattr(intent_router.reminders, "add_note", fake_add_note)

    result = await intent_router.maybe_run_tool(_USER, "ghi chú giúp anh mua sữa")

    assert captured["content"] == "mua sữa"
    assert "mua sữa" in result


async def test_maybe_run_tool_list_notes(monkeypatch):
    async def fake_text(task_type, messages, *, system, temperature=0.5):
        return AIResponse(text=json.dumps({"tool": "list_notes", "args": {}}), provider="t", model="t")

    async def fake_list_notes(user_id):
        return "1. mua sữa"

    monkeypatch.setattr(intent_router.router, "text", fake_text)
    monkeypatch.setattr(intent_router.reminders, "list_notes", fake_list_notes)

    result = await intent_router.maybe_run_tool(_USER, "em ghi chú gì rồi")

    assert result == "1. mua sữa"


async def test_maybe_run_tool_set_reminder(monkeypatch):
    captured = {}

    async def fake_text(task_type, messages, *, system, temperature=0.5):
        return AIResponse(
            text=json.dumps({"tool": "set_reminder", "args": {"spec": "20:00", "content": "gọi mẹ"}}),
            provider="t", model="t",
        )

    async def fake_add_reminder(user_id, spec, content):
        captured["spec"] = spec
        captured["content"] = content
        return "Đã đặt nhắc nhở."

    monkeypatch.setattr(intent_router.router, "text", fake_text)
    monkeypatch.setattr(intent_router.reminders, "add_reminder", fake_add_reminder)

    result = await intent_router.maybe_run_tool(_USER, "8 giờ tối nhắc anh gọi mẹ")

    assert captured == {"spec": "20:00", "content": "gọi mẹ"}
    assert result == "Đã đặt nhắc nhở."


async def test_maybe_run_tool_list_reminders(monkeypatch):
    async def fake_text(task_type, messages, *, system, temperature=0.5):
        return AIResponse(text=json.dumps({"tool": "list_reminders", "args": {}}), provider="t", model="t")

    async def fake_list_reminders(user_id):
        return "1. gọi mẹ lúc 20:00"

    monkeypatch.setattr(intent_router.router, "text", fake_text)
    monkeypatch.setattr(intent_router.reminders, "list_reminders", fake_list_reminders)

    result = await intent_router.maybe_run_tool(_USER, "xem nhắc nhở của anh")

    assert result == "1. gọi mẹ lúc 20:00"


async def test_maybe_run_tool_returns_none_on_provider_error(monkeypatch):
    async def fake_text(task_type, messages, *, system, temperature=0.5):
        raise ProviderError("timeout")

    monkeypatch.setattr(intent_router.router, "text", fake_text)

    assert await intent_router.maybe_run_tool(_USER, "ghi chú giúp anh mua sữa") is None


async def test_maybe_run_tool_returns_none_on_invalid_json(monkeypatch):
    async def fake_text(task_type, messages, *, system, temperature=0.5):
        return AIResponse(text="không phải JSON", provider="t", model="t")

    monkeypatch.setattr(intent_router.router, "text", fake_text)

    assert await intent_router.maybe_run_tool(_USER, "ghi chú giúp anh mua sữa") is None


async def test_maybe_run_tool_returns_none_for_unknown_tool_name(monkeypatch):
    async def fake_text(task_type, messages, *, system, temperature=0.5):
        return AIResponse(text=json.dumps({"tool": "delete_everything", "args": {}}), provider="t", model="t")

    monkeypatch.setattr(intent_router.router, "text", fake_text)

    assert await intent_router.maybe_run_tool(_USER, "abc") is None


async def test_maybe_run_tool_save_note_ignores_empty_content(monkeypatch):
    async def fake_text(task_type, messages, *, system, temperature=0.5):
        return AIResponse(text=json.dumps({"tool": "save_note", "args": {"content": ""}}), provider="t", model="t")

    monkeypatch.setattr(intent_router.router, "text", fake_text)

    assert await intent_router.maybe_run_tool(_USER, "ghi chú") is None
