from unittest.mock import AsyncMock, MagicMock

import pytest

from ai.contracts import ProviderError, ProviderUnavailable
from ai.providers.google import GoogleProvider, _to_gemini_contents


def test_to_gemini_contents_maps_assistant_role_to_model():
    messages = [
        {"role": "user", "content": "chào"},
        {"role": "assistant", "content": "chào bạn"},
        {"role": "user", "content": "  "},  # nội dung rỗng bị bỏ qua
    ]
    contents = _to_gemini_contents(messages)
    assert [c.role for c in contents] == ["user", "model"]


async def test_generate_raises_when_unconfigured():
    provider = GoogleProvider(api_key="", model="gemini-3.6-flash", concurrency=1)
    with pytest.raises(ProviderUnavailable):
        await provider.generate([{"role": "user", "content": "hi"}], system="s")


async def test_generate_raises_when_no_content():
    provider = GoogleProvider(api_key="", model="gemini-3.6-flash", concurrency=1)
    provider.client = MagicMock()  # giả lập đã cấu hình
    with pytest.raises(ProviderError):
        await provider.generate([{"role": "user", "content": "   "}], system="s")


async def test_generate_returns_text_from_client():
    provider = GoogleProvider(api_key="", model="gemini-3.6-flash", concurrency=1)
    fake_response = MagicMock()
    fake_response.text = "  câu trả lời  "
    provider.client = MagicMock()
    provider.client.aio.models.generate_content = AsyncMock(return_value=fake_response)

    result = await provider.generate(
        [{"role": "user", "content": "hi"}], system="s", temperature=0.3, max_tokens=100
    )

    assert result.text == "câu trả lời"
    assert result.provider == "google"
    assert result.model == "gemini-3.6-flash"
    provider.client.aio.models.generate_content.assert_awaited_once()


async def test_generate_wraps_empty_response_as_provider_error():
    provider = GoogleProvider(api_key="", model="gemini-3.6-flash", concurrency=1)
    fake_response = MagicMock()
    fake_response.text = ""
    provider.client = MagicMock()
    provider.client.aio.models.generate_content = AsyncMock(return_value=fake_response)

    with pytest.raises(ProviderError):
        await provider.generate([{"role": "user", "content": "hi"}], system="s")
