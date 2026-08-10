import services.commands as commands
from core.models import Channel, Role, User

_USER = User("u1", Channel.TELEGRAM, "1", Role.ROOT)


async def test_prompt_command_uses_shared_prompt_engine(monkeypatch):
    captured = {}

    async def fake_text_to_prompt(user, request):
        captured["request"] = request
        return "final prompt", "groq"

    monkeypatch.setattr(commands, "text_to_prompt", fake_text_to_prompt)
    result = await commands.handle(_USER, "/prompt giữ mặt tôi, đứng bên biển")
    assert "final prompt" in result
    assert "Prompt gợi ý" in result
    assert captured["request"] == "giữ mặt tôi, đứng bên biển"


async def test_prompt_command_requires_description():
    result = await commands.handle(_USER, "/prompt")
    assert "Cú pháp" in result


async def test_prompt_command_handles_provider_error(monkeypatch):
    from ai.contracts import ProviderError

    async def fail(user, request):
        raise ProviderError("timeout")

    monkeypatch.setattr(commands, "text_to_prompt", fail)
    result = await commands.handle(_USER, "/prompt cô gái bên biển")
    assert "provider AI" in result
