from channels.zalo import is_group_command
from core.models import Channel, Role, User


def test_only_admin_can_summarize_groups():
    admin = User("1", Channel.ZALO, "admin", Role.ZALO_ADMIN)
    normal = User("2", Channel.ZALO, "user", Role.USER)
    assert admin.can_use_group_summary
    assert not normal.can_use_group_summary


def test_group_command_detection():
    assert is_group_command("/tongket room 24h")
    assert is_group_command("/nhom")
    assert not is_group_command("xin chào")


def test_group_command_empty_text_does_not_crash():
    assert not is_group_command("")
    assert not is_group_command("   ")

async def test_unpaired_zalo_user_is_ignored(monkeypatch):
    import web
    from channels.zalo import ZaloEvent

    user = User("1", Channel.ZALO, "user", Role.USER, paired=False)

    async def fake_resolve_user(sender_id):
        return user

    monkeypatch.setattr(web, "resolve_user", fake_resolve_user)
    result = await web._handle_zalo_event(
        ZaloEvent(event_id="e1", sender_id="user", text="xin chào")
    )
    assert result.messages == []


async def test_paired_zalo_user_can_use_ai(monkeypatch):
    import web
    from channels.zalo import ZaloEvent

    user = User("1", Channel.ZALO, "user", Role.USER, paired=True)

    async def fake_resolve_user(sender_id):
        return user

    async def fake_command(user, text):
        return None

    async def fake_quote(text):
        return None

    async def fake_chat(user, text):
        return "hello", "provider"

    monkeypatch.setattr(web, "resolve_user", fake_resolve_user)
    monkeypatch.setattr(web.commands, "handle", fake_command)
    monkeypatch.setattr(web.commands, "try_ticker_quote", fake_quote)
    monkeypatch.setattr(web, "chat", fake_chat)
    result = await web._handle_zalo_event(
        ZaloEvent(event_id="e1", sender_id="user", text="xin chào")
    )
    assert result.messages == ["hello", "⚙️ provider"]
