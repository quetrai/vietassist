import bot
from core.models import Channel, Role, User


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.sent: list[tuple[str, str | None]] = []

    async def reply_text(self, content, parse_mode=None, **kwargs):
        self.sent.append((content, parse_mode))


class FakeUser:
    id = 12345


class FakeUpdate:
    def __init__(self, text: str):
        self.effective_message = FakeMessage(text)
        self.effective_user = FakeUser()


async def test_prompt_command_uses_code_block_for_body(monkeypatch):
    message = FakeMessage("/prompt cô gái mặc váy đỏ")
    update = FakeUpdate("/prompt cô gái mặc váy đỏ")
    update.effective_message = message

    async def fake_get_or_create_user(channel, external_id, role):
        return User("u1", Channel.TELEGRAM, "12345", Role.ROOT)

    async def fake_handle(user, text):
        return "📝 Prompt gợi ý\nDÙNG CHO APP GEMINI\n\nraw photo, *not italic*, snake_case_name"

    monkeypatch.setattr(bot.database, "get_or_create_user", fake_get_or_create_user)
    monkeypatch.setattr(bot.commands, "handle", fake_handle)

    await bot.command(update, None)

    assert len(message.sent) == 2
    header_text, header_mode = message.sent[0]
    body_text, body_mode = message.sent[1]
    assert "Prompt gợi ý" in header_text
    assert header_mode == "HTML"
    assert body_mode == "HTML"
    assert body_text.startswith("<pre>") and body_text.endswith("</pre>")
    # Nội dung prompt phải giữ NGUYÊN dấu * và _, không bị reply_rich() nuốt mất.
    assert "*not italic*" in body_text
    assert "snake_case_name" in body_text


async def test_prompt_command_error_falls_back_to_reply_rich(monkeypatch):
    message = FakeMessage("/prompt")
    update = FakeUpdate("/prompt")
    update.effective_message = message

    async def fake_get_or_create_user(channel, external_id, role):
        return User("u1", Channel.TELEGRAM, "12345", Role.ROOT)

    async def fake_handle(user, text):
        return "Cú pháp: /prompt <mô tả ảnh cần tạo>"

    monkeypatch.setattr(bot.database, "get_or_create_user", fake_get_or_create_user)
    monkeypatch.setattr(bot.commands, "handle", fake_handle)

    await bot.command(update, None)

    assert len(message.sent) == 1
    text, parse_mode = message.sent[0]
    assert "Cú pháp" in text


async def test_other_commands_still_use_reply_rich(monkeypatch):
    message = FakeMessage("/gia iphone 16")
    update = FakeUpdate("/gia iphone 16")
    update.effective_message = message

    async def fake_get_or_create_user(channel, external_id, role):
        return User("u1", Channel.TELEGRAM, "12345", Role.ROOT)

    async def fake_handle(user, text):
        return "**iPhone 16**: khoảng 20 triệu"

    monkeypatch.setattr(bot.database, "get_or_create_user", fake_get_or_create_user)
    monkeypatch.setattr(bot.commands, "handle", fake_handle)

    await bot.command(update, None)

    assert len(message.sent) == 1
    text, parse_mode = message.sent[0]
    assert parse_mode == "HTML"
    assert "<b>iPhone 16</b>" in text
