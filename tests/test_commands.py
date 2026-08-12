import services.commands as commands
from core.models import Channel, Role, User

_USER = User("u1", Channel.TELEGRAM, "1", Role.ROOT)


async def test_try_ticker_quote_matches_bare_3_letters(monkeypatch):
    captured = {}

    async def fake_quick_quote(symbol):
        captured["symbol"] = symbol
        return "FPT: 120.000đ (+1.23%) — phiên 2026-08-07"

    monkeypatch.setattr(commands, "quick_quote", fake_quick_quote)
    result = await commands.try_ticker_quote("fpt")
    assert result == "FPT: 120.000đ (+1.23%) — phiên 2026-08-07"
    assert captured["symbol"] == "fpt"


async def test_try_ticker_quote_ignores_non_3_letter_text(monkeypatch):
    async def fail_quick_quote(symbol):
        raise AssertionError("không được gọi quick_quote cho text không phải mã 3 chữ")

    monkeypatch.setattr(commands, "quick_quote", fail_quick_quote)
    assert await commands.try_ticker_quote("xin chào") is None
    assert await commands.try_ticker_quote("ê") is None
    assert await commands.try_ticker_quote("FPT2") is None
    assert await commands.try_ticker_quote("") is None


async def test_try_ticker_quote_falls_back_silently_on_error(monkeypatch):
    """Regression: chat ngẫu nhiên trùng 3 chữ cái (vd 'cho', 'khi') không được hiện lỗi tra
    giá, phải im lặng rơi về chat bình thường."""

    async def fake_quick_quote(symbol):
        raise ValueError("Mã cổ phiếu phải gồm 3 chữ cái")

    monkeypatch.setattr(commands, "quick_quote", fake_quick_quote)
    assert await commands.try_ticker_quote("cho") is None


async def test_rag_status_no_argument(monkeypatch):
    on_user = User("u1", Channel.TELEGRAM, "1", Role.ROOT, True, True)
    off_user = User("u1", Channel.TELEGRAM, "1", Role.ROOT, True, False)
    assert "BẬT" in await commands.handle(on_user, "/rag")
    assert "TẮT" in await commands.handle(off_user, "/rag")


async def test_rag_on_persists_via_database(monkeypatch):
    captured = {}

    async def fake_set_rag_enabled(user_id, enabled):
        captured.update(user_id=user_id, enabled=enabled)

    monkeypatch.setattr(commands.database, "set_rag_enabled", fake_set_rag_enabled)
    result = await commands.handle(_USER, "/rag on")
    assert "Đã bật" in result
    assert captured == {"user_id": "u1", "enabled": True}


async def test_rag_off_persists_via_database(monkeypatch):
    captured = {}

    async def fake_set_rag_enabled(user_id, enabled):
        captured.update(user_id=user_id, enabled=enabled)

    monkeypatch.setattr(commands.database, "set_rag_enabled", fake_set_rag_enabled)
    result = await commands.handle(_USER, "/rag off")
    assert "Đã tắt" in result
    assert captured == {"user_id": "u1", "enabled": False}


async def test_stock_without_deep_flag(monkeypatch):
    captured = {}

    async def fake_analyze_symbol(symbol, *, holding=False, deep=False):
        captured.update(symbol=symbol, holding=holding, deep=deep)
        return "ok"

    async def fake_is_holding(user_id, symbol):
        return False

    monkeypatch.setattr(commands, "analyze_symbol", fake_analyze_symbol)
    monkeypatch.setattr(commands.database, "is_holding", fake_is_holding)

    result = await commands.handle(_USER, "/stock FPT")
    assert result == "ok"
    assert captured == {"symbol": "FPT", "holding": False, "deep": False}


async def test_stock_with_deep_flag_sau(monkeypatch):
    captured = {}

    async def fake_analyze_symbol(symbol, *, holding=False, deep=False):
        captured.update(deep=deep)
        return "ok"

    async def fake_is_holding(user_id, symbol):
        return False

    monkeypatch.setattr(commands, "analyze_symbol", fake_analyze_symbol)
    monkeypatch.setattr(commands.database, "is_holding", fake_is_holding)

    await commands.handle(_USER, "/stock FPT sau")
    assert captured["deep"] is True


async def test_stock_with_deep_flag_dau_tieng_viet(monkeypatch):
    captured = {}

    async def fake_analyze_symbol(symbol, *, holding=False, deep=False):
        captured.update(deep=deep)
        return "ok"

    async def fake_is_holding(user_id, symbol):
        return False

    monkeypatch.setattr(commands, "analyze_symbol", fake_analyze_symbol)
    monkeypatch.setattr(commands.database, "is_holding", fake_is_holding)

    await commands.handle(_USER, "/stock FPT sâu")
    assert captured["deep"] is True


async def test_stock_marks_holding_true_when_in_portfolio(monkeypatch):
    captured = {}

    async def fake_analyze_symbol(symbol, *, holding=False, deep=False):
        captured.update(holding=holding)
        return "ok"

    async def fake_is_holding(user_id, symbol):
        assert symbol == "FPT"
        return True

    monkeypatch.setattr(commands, "analyze_symbol", fake_analyze_symbol)
    monkeypatch.setattr(commands.database, "is_holding", fake_is_holding)

    await commands.handle(_USER, "/stock fpt")
    assert captured["holding"] is True


async def test_vimo_requires_argument():
    result = await commands.handle(_USER, "/vimo")
    assert "Cú pháp" in result


async def test_vimo_calls_router_macro_news(monkeypatch):
    from ai.contracts import AIResponse

    async def fake_macro_news(query):
        assert query == "lãi suất điều hành"
        return AIResponse(text="Tin tức...", provider="google", model="m", grounded=True)

    monkeypatch.setattr(commands.router, "macro_news", fake_macro_news)
    result = await commands.handle(_USER, "/vimo lãi suất điều hành")
    assert result == "Tin tức..."


async def test_dich_requires_argument():
    result = await commands.handle(_USER, "/dich")
    assert "Cú pháp" in result


async def test_dich_auto_detects_direction(monkeypatch):
    captured = {}

    async def fake_translate(text, direction):
        captured.update(text=text, direction=direction)
        return "Đã hiểu rồi.", "openrouter", "ja_vi"

    monkeypatch.setattr(commands.translate_service, "translate", fake_translate)
    result = await commands.handle(_USER, "/dich 了解しました。")
    assert captured == {"text": "了解しました。", "direction": None}
    assert "Đã hiểu rồi." in result
    assert "Nhật → Tiếng Việt" in result


async def test_dich_with_explicit_direction_prefix(monkeypatch):
    captured = {}

    async def fake_translate(text, direction):
        captured.update(text=text, direction=direction)
        return "了解しました。", "groq", "vi_ja"

    monkeypatch.setattr(commands.translate_service, "translate", fake_translate)
    result = await commands.handle(_USER, "/dich vi>ja đã hiểu rồi, cảm ơn nhé")
    assert captured == {"text": "đã hiểu rồi, cảm ơn nhé", "direction": "vi_ja"}
    assert "了解しました。" in result


async def test_dich_direction_prefix_without_content_asks_for_syntax():
    result = await commands.handle(_USER, "/dich ja>vi")
    assert "Cú pháp" in result


async def test_dich_reports_friendly_message_on_provider_error(monkeypatch):
    from ai.contracts import ProviderError

    async def fake_translate(text, direction):
        raise ProviderError("openrouter: TimeoutError")

    monkeypatch.setattr(commands.translate_service, "translate", fake_translate)
    result = await commands.handle(_USER, "/dich xin chào")
    assert "Không dịch được" in result


async def test_unrecognized_command_returns_none():
    assert await commands.handle(_USER, "/khonghieu") is None


async def test_gia_reports_friendly_message_when_not_configured(monkeypatch):
    from ai.contracts import GroundingUnavailable

    async def fake_product_search(query):
        raise GroundingUnavailable("chưa cấu hình")

    monkeypatch.setattr(commands.router, "product_search", fake_product_search)
    result = await commands.handle(_USER, "/gia iphone 16")
    assert "Chưa cấu hình" in result


async def test_gia_reports_friendly_message_on_generic_provider_error(monkeypatch):
    """Regression test: lỗi runtime (mạng, quota, 5xx...) khác GroundingUnavailable trước đây
    không bị bắt và làm văng exception thẳng ra ngoài; giờ phải trả về thông báo thân thiện."""
    from ai.contracts import ProviderError

    async def fake_product_search(query):
        raise ProviderError("google: TimeoutError")

    monkeypatch.setattr(commands.router, "product_search", fake_product_search)
    result = await commands.handle(_USER, "/gia iphone 16")
    assert "lỗi tạm thời" in result


async def test_vimo_reports_friendly_message_on_generic_provider_error(monkeypatch):
    from ai.contracts import ProviderError

    async def fake_macro_news(query):
        raise ProviderError("google: TimeoutError")

    monkeypatch.setattr(commands.router, "macro_news", fake_macro_news)
    result = await commands.handle(_USER, "/vimo lãi suất")
    assert "lỗi tạm thời" in result
