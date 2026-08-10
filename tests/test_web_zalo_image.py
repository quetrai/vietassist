import httpx

import web
from ai.contracts import AIResponse, ProviderError, ProviderUnavailable
from channels.zalo import ZaloEvent


def _event(**overrides) -> ZaloEvent:
    base = dict(
        event_id="e1",
        sender_id="u1",
        text="",
        kind="image",
        image_url="https://cdn.example.com/photo.jpg",
    )
    base.update(overrides)
    return ZaloEvent(**base)


async def test_handle_zalo_image_missing_url_returns_friendly_message():
    result = await web._handle_zalo_image(_event(image_url=None))
    assert "Không nhận được ảnh" in result.messages[0]


async def test_handle_zalo_image_download_failure(monkeypatch):
    async def fake_download_image(url):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(web, "download_image", fake_download_image)
    result = await web._handle_zalo_image(_event())
    assert "Không tải được ảnh" in result.messages[0]


async def test_handle_zalo_image_provider_unavailable(monkeypatch, tmp_path):
    fake_path = tmp_path / "photo.jpg"
    fake_path.write_bytes(b"x")

    async def fake_download_image(url):
        return str(fake_path)

    async def fake_image_prompt(path, instruction):
        raise ProviderUnavailable("chưa cấu hình")

    monkeypatch.setattr(web, "download_image", fake_download_image)
    monkeypatch.setattr(web.router, "image_prompt", fake_image_prompt)
    result = await web._handle_zalo_image(_event())
    assert "Chưa cấu hình Google Gemini" in result.messages[0]
    assert not fake_path.exists()  # file tạm phải được dọn dù lỗi


async def test_handle_zalo_image_provider_error(monkeypatch, tmp_path):
    fake_path = tmp_path / "photo.jpg"
    fake_path.write_bytes(b"x")

    async def fake_download_image(url):
        return str(fake_path)

    async def fake_image_prompt(path, instruction):
        raise ProviderError("lỗi tạm thời")

    monkeypatch.setattr(web, "download_image", fake_download_image)
    monkeypatch.setattr(web.router, "image_prompt", fake_image_prompt)
    result = await web._handle_zalo_image(_event())
    assert "lỗi tạm thời" in result.messages[0].lower()


async def test_handle_zalo_image_success_uses_caption_as_instruction(monkeypatch, tmp_path):
    fake_path = tmp_path / "photo.jpg"
    fake_path.write_bytes(b"x")
    received = {}

    async def fake_download_image(url):
        return str(fake_path)

    async def fake_image_prompt(path, instruction):
        received["instruction"] = instruction
        return AIResponse("prompt sinh ra", "google", "gemini")

    monkeypatch.setattr(web, "download_image", fake_download_image)
    monkeypatch.setattr(web.router, "image_prompt", fake_image_prompt)
    result = await web._handle_zalo_image(_event(text="ảnh phong cảnh biển"))
    assert result.messages[0].startswith("📝 Prompt gợi ý")
    assert "prompt sinh ra" in result.messages[0]
    assert "<user_request>" in received["instruction"]
    assert "ảnh phong cảnh biển" in received["instruction"]
    assert not fake_path.exists()


async def test_handle_zalo_image_success_uses_default_instruction_without_caption(
    monkeypatch, tmp_path
):
    fake_path = tmp_path / "photo.jpg"
    fake_path.write_bytes(b"x")
    received = {}

    async def fake_download_image(url):
        return str(fake_path)

    async def fake_image_prompt(path, instruction):
        received["instruction"] = instruction
        return AIResponse("prompt sinh ra", "google", "gemini")

    monkeypatch.setattr(web, "download_image", fake_download_image)
    monkeypatch.setattr(web.router, "image_prompt", fake_image_prompt)
    result = await web._handle_zalo_image(_event(text=""))
    assert "<user_request>" in received["instruction"]
    assert "no additional request" in received["instruction"]
    assert "prompt sinh ra" in result.messages[0]
