import services.commands as commands
from core.models import Channel, Role, User

_USER = User("u1", Channel.TELEGRAM, "1", Role.ROOT)


async def test_ghichu_with_content_adds_note(monkeypatch):
    captured = {}

    async def fake_add_note(user_id, content):
        captured["content"] = content
        return "Đã lưu ghi chú."

    async def fail_list_notes(user_id):
        raise AssertionError("không được gọi list_notes khi có nội dung")

    monkeypatch.setattr(commands.reminders, "add_note", fake_add_note)
    monkeypatch.setattr(commands.reminders, "list_notes", fail_list_notes)

    result = await commands.handle(_USER, "/ghichu mua sữa")

    assert result == "Đã lưu ghi chú."
    assert captured["content"] == "mua sữa"


async def test_ghichu_without_content_lists_notes(monkeypatch):
    async def fail_add_note(user_id, content):
        raise AssertionError("không được gọi add_note khi để trống")

    async def fake_list_notes(user_id):
        return "1. mua sữa"

    monkeypatch.setattr(commands.reminders, "add_note", fail_add_note)
    monkeypatch.setattr(commands.reminders, "list_notes", fake_list_notes)

    result = await commands.handle(_USER, "/ghichu")

    assert result == "1. mua sữa"


async def test_nhac_with_content_adds_reminder(monkeypatch):
    captured = {}

    async def fake_add_reminder(user_id, spec, content):
        captured["spec"] = spec
        captured["content"] = content
        return "Đã đặt nhắc nhở."

    async def fail_list_reminders(user_id):
        raise AssertionError("không được gọi list_reminders khi có nội dung")

    monkeypatch.setattr(commands.reminders, "add_reminder", fake_add_reminder)
    monkeypatch.setattr(commands.reminders, "list_reminders", fail_list_reminders)

    result = await commands.handle(_USER, "/nhac 30p uống thuốc")

    assert result == "Đã đặt nhắc nhở."
    assert captured["spec"] == "30p"
    assert captured["content"] == "uống thuốc"


async def test_nhac_without_content_lists_reminders(monkeypatch):
    async def fail_add_reminder(user_id, spec, content):
        raise AssertionError("không được gọi add_reminder khi để trống")

    async def fake_list_reminders(user_id):
        return "1. uống thuốc lúc 20:00"

    monkeypatch.setattr(commands.reminders, "add_reminder", fail_add_reminder)
    monkeypatch.setattr(commands.reminders, "list_reminders", fake_list_reminders)

    result = await commands.handle(_USER, "/nhac")

    assert result == "1. uống thuốc lúc 20:00"


async def test_dsghichu_alias_still_works(monkeypatch):
    async def fake_list_notes(user_id):
        return "1. mua sữa"

    monkeypatch.setattr(commands.reminders, "list_notes", fake_list_notes)
    assert await commands.handle(_USER, "/dsghichu") == "1. mua sữa"


async def test_dsnhac_alias_still_works(monkeypatch):
    async def fake_list_reminders(user_id):
        return "1. uống thuốc"

    monkeypatch.setattr(commands.reminders, "list_reminders", fake_list_reminders)
    assert await commands.handle(_USER, "/dsnhac") == "1. uống thuốc"


async def test_muctieu_calls_portfolio_set_alerts(monkeypatch):
    captured = {}

    async def fake_set_alerts(user_id, symbol_raw, stop_raw, target_raw):
        captured["args"] = (user_id, symbol_raw, stop_raw, target_raw)
        return "Đã cập nhật FPT: stop 20.000đ, target 30.000đ."

    monkeypatch.setattr(commands.portfolio, "set_alerts", fake_set_alerts)

    result = await commands.handle(_USER, "/muctieu FPT 20000 30000")

    assert captured["args"] == ("u1", "FPT", "20000", "30000")
    assert "Đã cập nhật FPT" in result


async def test_muctieu_reports_syntax_error_on_missing_args():
    result = await commands.handle(_USER, "/muctieu FPT 20000")
    assert "Cú pháp" in result
