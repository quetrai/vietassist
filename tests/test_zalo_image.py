from pathlib import Path

import httpx
import pytest

import channels.zalo as zalo_channel


class _FakeResponse:
    def __init__(self, content: bytes, status_ok: bool = True):
        self.content = content
        self._status_ok = status_ok
        self.headers = {"content-type": "image/jpeg", "content-length": str(len(content))}
        self.is_redirect = False
        self.url = httpx.URL("https://cdn.example.com/anh/photo.jpg")

    def raise_for_status(self):
        if not self._status_ok:
            raise httpx.HTTPStatusError("boom", request=None, response=None)


class _FakeAsyncClient:
    def __init__(self, content: bytes, status_ok: bool = True):
        self._content = content
        self._status_ok = status_ok

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        return _FakeResponse(self._content, self._status_ok)


async def test_download_image_saves_to_temp_file(monkeypatch):
    async def allow_host(url):
        return None

    monkeypatch.setattr(zalo_channel, "_validate_image_url", allow_host)
    monkeypatch.setattr(
        zalo_channel.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(b"fake-bytes")
    )
    path = await zalo_channel.download_image("https://cdn.example.com/anh/photo.png?ext=1")
    try:
        assert Path(path).read_bytes() == b"fake-bytes"
        assert Path(path).suffix == ".png"
    finally:
        Path(path).unlink(missing_ok=True)


async def test_download_image_unknown_extension_defaults_to_jpg(monkeypatch):
    async def allow_host(url):
        return None

    monkeypatch.setattr(zalo_channel, "_validate_image_url", allow_host)
    monkeypatch.setattr(zalo_channel.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(b"x"))
    path = await zalo_channel.download_image("https://cdn.example.com/anh/photo?id=1")
    try:
        assert Path(path).suffix == ".jpg"
    finally:
        Path(path).unlink(missing_ok=True)


async def test_download_image_raises_on_http_error(monkeypatch):
    async def allow_host(url):
        return None

    monkeypatch.setattr(zalo_channel, "_validate_image_url", allow_host)
    monkeypatch.setattr(
        zalo_channel.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(b"", status_ok=False)
    )
    with pytest.raises(httpx.HTTPStatusError):
        await zalo_channel.download_image("https://cdn.example.com/anh/photo.jpg")


async def test_download_image_rejects_non_https(monkeypatch):
    with pytest.raises(ValueError, match="HTTPS"):
        await zalo_channel.download_image("http://cdn.example.com/photo.jpg")


async def test_download_image_rejects_private_resolution(monkeypatch):
    async def resolve_private(url):
        raise ValueError("Image URL resolves to a private or reserved address")

    monkeypatch.setattr(zalo_channel, "_validate_image_url", resolve_private)
    with pytest.raises(ValueError, match="private"):
        await zalo_channel.download_image("https://cdn.example.com/photo.jpg")
