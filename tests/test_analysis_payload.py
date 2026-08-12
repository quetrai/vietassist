import json
from datetime import date, timedelta

import stock.analysis as analysis
from ai.contracts import AIResponse
from stock.market import Series


def _series(closes: list[float], dates: list[str], source: str = "dnse") -> Series:
    n = len(closes)
    return Series("FPT", closes, closes, closes, [1000.0] * n, dates, source)


def _business_dates(n: int, start: date | None = None) -> list[str]:
    """Sinh n ngày tăng dần liên tiếp (không cần đúng lịch giao dịch thật, chỉ cần
    tăng nghiêm ngặt để qua được stock/validation.py). Mặc định kết thúc ở HÔM NAY
    (không phải mốc cố định) để không bị validation.py coi là "dữ liệu cũ" khi chạy
    vào một ngày xa ngày viết test."""
    if start is None:
        start = date.today() - timedelta(days=n - 1)
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def _long_enough_series(base: float = 100.0, days: int = 55) -> Series:
    closes = [base + i * 0.1 for i in range(days)]
    return _series(closes, _business_dates(days))


async def _patch_common(monkeypatch, *, fetch_pair, fundamentals_payload=None, symbol_news=None):
    async def fake_fundamentals_payload(symbol):
        return fundamentals_payload

    async def fake_symbol_news(symbol):
        return symbol_news

    captured: dict[str, object] = {}

    async def fake_text(task_type, messages, **kwargs):
        captured["content"] = json.loads(messages[0]["content"])
        return AIResponse(text="ok", provider="test", model="test")

    monkeypatch.setattr(analysis, "fetch_pair", fetch_pair)
    monkeypatch.setattr(analysis, "_safe_fundamentals_payload", fake_fundamentals_payload)
    monkeypatch.setattr(analysis, "_safe_symbol_news", fake_symbol_news)
    monkeypatch.setattr(analysis.router, "text", fake_text)
    return captured


async def test_analyze_symbol_includes_fundamentals_in_payload(monkeypatch):
    async def fake_fetch_pair(symbol, **kwargs):
        return _long_enough_series(), _long_enough_series(base=1000.0)

    captured = await _patch_common(
        monkeypatch, fetch_pair=fake_fetch_pair,
        fundamentals_payload={"sector": "Ngân hàng", "valuation": {"pb": 1.9}},
    )

    result = await analysis.analyze_symbol("fpt")

    assert result == "ok"
    assert captured["content"]["fundamentals"] == {"sector": "Ngân hàng", "valuation": {"pb": 1.9}}
    assert "price_adjustment_warning" not in captured["content"]
    assert "data_quality_warning" not in captured["content"]


async def test_analyze_symbol_includes_price_adjustment_warning(monkeypatch):
    # Chuỗi có gap -50% (chia tách/thưởng chưa điều chỉnh) ở giữa, đủ dài (>=50
    # phiên) để qua được các yêu cầu tối thiểu của features.calculate.
    closes = [100.0 + i * 0.1 for i in range(30)] + [50.0 + i * 0.1 for i in range(30)]
    gapped_series = _series(closes, _business_dates(60))

    async def fake_fetch_pair(symbol, **kwargs):
        return gapped_series, _long_enough_series(base=1000.0, days=60)

    captured = await _patch_common(monkeypatch, fetch_pair=fake_fetch_pair)

    await analysis.analyze_symbol("fpt")

    assert "CHƯA ĐIỀU CHỈNH" in captured["content"]["price_adjustment_warning"]


async def test_analyze_symbol_includes_data_quality_warning_when_degraded(monkeypatch):
    # Đủ bar để qua hard floor (>=50 cho sma50) nhưng dữ liệu CŨ — phiên gần nhất
    # cách xa "hôm nay" (validation.py dùng datetime.now() thật) nên chắc chắn stale.
    stale_series = _series(
        [100.0 + i * 0.1 for i in range(55)], _business_dates(55, start=date(2020, 1, 1))
    )

    async def fake_fetch_pair(symbol, **kwargs):
        return stale_series, _long_enough_series(base=1000.0, days=60)

    captured = await _patch_common(monkeypatch, fetch_pair=fake_fetch_pair)

    await analysis.analyze_symbol("fpt")

    assert any("cũ" in reason for reason in captured["content"]["data_quality_warning"])


async def test_analyze_symbol_raises_when_data_quality_bad(monkeypatch):
    async def fake_fetch_pair(symbol, **kwargs):
        # Chỉ 5 phiên — dưới MIN_BARS_HARD_FLOOR (20), phải chặn hẳn trước khi tính chỉ báo.
        tiny = _series([100.0, 101.0, 102.0, 103.0, 104.0], _business_dates(5))
        return tiny, _long_enough_series(base=1000.0, days=60)

    monkeypatch.setattr(analysis, "fetch_pair", fake_fetch_pair)

    try:
        await analysis.analyze_symbol("fpt")
        raise AssertionError("phải raise RuntimeError khi dữ liệu quá ít phiên")
    except RuntimeError as exc:
        assert "phiên" in str(exc)


async def test_analyze_symbol_includes_recent_news_when_available(monkeypatch):
    async def fake_fetch_pair(symbol, **kwargs):
        return _long_enough_series(), _long_enough_series(base=1000.0)

    captured = await _patch_common(
        monkeypatch, fetch_pair=fake_fetch_pair, symbol_news="FPT vừa công bố KQKD quý gần nhất."
    )

    await analysis.analyze_symbol("fpt")

    assert captured["content"]["recent_news"] == "FPT vừa công bố KQKD quý gần nhất."


async def test_analyze_symbol_omits_recent_news_when_unavailable(monkeypatch):
    async def fake_fetch_pair(symbol, **kwargs):
        return _long_enough_series(), _long_enough_series(base=1000.0)

    captured = await _patch_common(monkeypatch, fetch_pair=fake_fetch_pair, symbol_news=None)

    await analysis.analyze_symbol("fpt")

    assert "recent_news" not in captured["content"]
