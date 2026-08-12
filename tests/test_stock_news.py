from ai.contracts import AIResponse, GroundingUnavailable, ProviderError
from stock import news


async def test_fetch_symbol_news_returns_trimmed_text(monkeypatch):
    news._cache.clear()

    async def fake_macro_news(query):
        assert "FPT" in query
        return AIResponse(text="  Tin tức mới về FPT.  ", provider="test", model="test")

    monkeypatch.setattr(news.router, "macro_news", fake_macro_news)

    result = await news.fetch_symbol_news("fpt")

    assert result == "Tin tức mới về FPT."


async def test_fetch_symbol_news_returns_none_on_grounding_unavailable(monkeypatch):
    news._cache.clear()

    async def fake_macro_news(query):
        raise GroundingUnavailable("chưa cấu hình")

    monkeypatch.setattr(news.router, "macro_news", fake_macro_news)

    assert await news.fetch_symbol_news("FPT") is None


async def test_fetch_symbol_news_returns_none_on_provider_error(monkeypatch):
    news._cache.clear()

    async def fake_macro_news(query):
        raise ProviderError("timeout")

    monkeypatch.setattr(news.router, "macro_news", fake_macro_news)

    assert await news.fetch_symbol_news("FPT") is None


async def test_fetch_symbol_news_truncates_long_text(monkeypatch):
    news._cache.clear()
    long_text = "A" * 2000

    async def fake_macro_news(query):
        return AIResponse(text=long_text, provider="test", model="test")

    monkeypatch.setattr(news.router, "macro_news", fake_macro_news)

    result = await news.fetch_symbol_news("FPT")

    assert result is not None
    assert len(result) == news._MAX_CHARS


async def test_fetch_symbol_news_uses_cache_within_ttl(monkeypatch):
    news._cache.clear()
    calls = {"count": 0}

    async def fake_macro_news(query):
        calls["count"] += 1
        return AIResponse(text="tin tức", provider="test", model="test")

    monkeypatch.setattr(news.router, "macro_news", fake_macro_news)

    await news.fetch_symbol_news("FPT")
    await news.fetch_symbol_news("fpt")  # cùng mã (khác hoa/thường) -> phải dùng cache

    assert calls["count"] == 1
