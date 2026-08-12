from datetime import datetime

import stock.analysis as analysis
from stock.market import VN_TZ, Series


def _series(dates: list[str], closes: list[float]) -> Series:
    n = len(dates)
    return Series("FPT", closes, closes, closes, [1000.0] * n, dates)


async def test_quick_quote_uses_realtime_tick_when_available(monkeypatch):
    today = datetime.now(VN_TZ).strftime("%Y-%m-%d")

    async def fake_fetch(symbol, **kwargs):
        # OHLC chưa có nến hôm nay (chỉ có tới hôm qua) -> giá trong ngày phải
        # đến từ tick realtime, không phải từ closes[-1] (giá đóng cửa hôm qua).
        return _series(["2026-08-05", "2026-08-06"], [24000.0, 24500.0])

    async def fake_tick(symbol):
        return 25200.0

    monkeypatch.setattr(analysis, "fetch", fake_fetch)
    monkeypatch.setattr(analysis, "fetch_realtime_tick", fake_tick)

    result = await analysis.quick_quote("fpt")

    assert "25.200đ" in result
    assert "realtime" in result
    assert today in result
    # % thay đổi phải so với phiên gần nhất (24.500), không phải giá cũ hơn.
    assert "+2.86%" in result


async def test_quick_quote_falls_back_to_ohlc_close_without_tick(monkeypatch):
    async def fake_fetch(symbol, **kwargs):
        return _series(["2026-08-05", "2026-08-06"], [24000.0, 24500.0])

    async def fake_tick(symbol):
        return None

    monkeypatch.setattr(analysis, "fetch", fake_fetch)
    monkeypatch.setattr(analysis, "fetch_realtime_tick", fake_tick)

    result = await analysis.quick_quote("fpt")

    assert "24.500đ" in result
    assert "realtime" not in result
    assert "2026-08-06" in result
