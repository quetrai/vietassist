from datetime import datetime, timedelta

import httpx
import pytest

import stock.market as market


class _FailingClient:
    def __init__(self, fail_times: int = 999) -> None:
        self.calls = 0
        self.fail_times = fail_times

    async def get(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise httpx.ConnectError("boom")
        raise httpx.ConnectError("still down")


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def get(self, *_args, **_kwargs) -> _FakeResponse:
        return _FakeResponse(self._payload)


class _WindowedFakeClient:
    """Giả lập API thật: chỉ trả về các phiên nằm trong khoảng from/to được request,
    để test được việc cửa sổ request có đủ rộng hay không (khác _FakeClient bỏ qua params)."""

    def __init__(self, full_payload: dict) -> None:
        self._full = full_payload

    async def get(self, _url: str, params: dict) -> _FakeResponse:
        idx = [i for i, t in enumerate(self._full["t"]) if params["from"] <= t <= params["to"]]
        payload = {key: [self._full[key][i] for i in idx] for key in ("t", "c", "h", "l", "v")}
        return _FakeResponse(payload)


async def test_fetch_aligns_arrays_of_unequal_length(monkeypatch):
    # "t" có 31 phần tử, các mảng còn lại chỉ 30 -> nếu cắt [-days:] độc lập
    # (bug cũ) thì "t" sẽ ứng với phiên khác các mảng còn lại.
    size = 30
    start = 1_700_000_000
    extra_t = [start + i * 86400 for i in range(size + 1)]
    payload = {
        "t": extra_t,
        "c": [float(i) + 10 for i in range(size)],
        "h": [float(i) + 11 for i in range(size)],
        "l": [float(i) + 9 for i in range(size)],
        "v": [1000.0 for _ in range(size)],
    }
    monkeypatch.setattr(market, "client", lambda: _FakeClient(payload))
    market._cache.clear()

    series = await market.fetch("AAA", days=size, ttl=90)

    assert len(series.dates) == size
    # Phiên cuối cùng của "t" (đã cắt đồng bộ) phải khớp với phiên cuối của "c"
    assert (
        series.dates[-1]
        == market.datetime.fromtimestamp(extra_t[-1], market.UTC).date().isoformat()
    )
    assert series.closes[-1] == (size - 1 + 10) * 1000


async def test_fetch_with_small_days_still_requests_enough_sessions(monkeypatch):
    """Regression: quick_quote gọi fetch(days=5). Nếu cửa sổ request chỉ dựa theo `days`
    (bug cũ), API chỉ trả ~10 ngày lịch (~7 phiên) -> luôn dính `size < MIN_SESSIONS`."""
    total = 40
    now = int(market.datetime.now(market.UTC).timestamp())
    full_payload = {
        "t": [now - (total - 1 - i) * 86400 for i in range(total)],
        "c": [float(i) + 10 for i in range(total)],
        "h": [float(i) + 11 for i in range(total)],
        "l": [float(i) + 9 for i in range(total)],
        "v": [1000.0 for _ in range(total)],
    }
    monkeypatch.setattr(market, "client", lambda: _WindowedFakeClient(full_payload))
    market._cache.clear()

    series = await market.fetch("AAA", days=5, ttl=90)

    assert len(series.closes) == 5
    assert series.closes[-1] == (total - 1 + 10) * 1000


async def test_dnse_retries_transient_failure_then_succeeds(monkeypatch):
    size = 30
    start = 1_700_000_000
    payload = {
        "t": [start + i * 86400 for i in range(size)],
        "c": [float(i) + 10 for i in range(size)],
        "h": [float(i) + 11 for i in range(size)],
        "l": [float(i) + 9 for i in range(size)],
        "v": [1000.0 for _ in range(size)],
    }

    class _FlakyClient:
        def __init__(self) -> None:
            self.calls = 0

        async def get(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise httpx.ConnectError("boom")
            return _FakeResponse(payload)

    flaky = _FlakyClient()
    monkeypatch.setattr(market, "DNSE_RETRY_BACKOFF_SEC", 0)
    monkeypatch.setattr(market, "client", lambda: flaky)
    market._cache.clear()

    series = await market.fetch("AAA", days=size, ttl=90)

    assert series.source == "dnse"
    assert len(series.closes) == size


async def test_dnse_exhausted_falls_back_to_vnstock(monkeypatch):
    monkeypatch.setattr(market, "DNSE_RETRY_BACKOFF_SEC", 0)
    monkeypatch.setattr(market, "client", lambda: _FailingClient())

    fallback_series = market.Series(
        "AAA",
        [10000] * 30,
        [10100] * 30,
        [9900] * 30,
        [1000.0] * 30,
        [f"2024-01-{i + 1:02d}" for i in range(30)],
        source="vnstock",
    )

    async def fake_vnstock(_symbol, _days):
        return fallback_series

    monkeypatch.setattr(market, "_fetch_from_vnstock", fake_vnstock)
    market._cache.clear()

    series = await market.fetch("AAA", days=30, ttl=90)

    assert series.source == "vnstock"
    assert series.closes[0] == 10000


async def test_both_sources_fail_raises(monkeypatch):
    monkeypatch.setattr(market, "DNSE_RETRY_BACKOFF_SEC", 0)
    monkeypatch.setattr(market, "client", lambda: _FailingClient())

    async def fake_vnstock_fail(_symbol, _days):
        raise RuntimeError("vnstock cũng lỗi")

    monkeypatch.setattr(market, "_fetch_from_vnstock", fake_vnstock_fail)
    market._cache.clear()

    with pytest.raises(RuntimeError):
        await market.fetch("AAA", days=30, ttl=90)


def test_trim_open_session_drops_todays_incomplete_bar(monkeypatch):
    today = datetime.now(market.VN_TZ)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return today.replace(hour=10, minute=0, second=0, microsecond=0)

    monkeypatch.setattr(market, "datetime", _FixedDateTime)
    dates = [(today - timedelta(days=i)).date().isoformat() for i in range(5, -1, -1)]
    series = market.Series(
        "AAA", [1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6], [1, 1, 1, 1, 1, 1], dates
    )

    trimmed = market.trim_open_session(series)

    assert len(trimmed.closes) == 5
    assert trimmed.dates[-1] != today.date().isoformat()


def test_trim_open_session_keeps_bar_after_market_close(monkeypatch):
    today = datetime.now(market.VN_TZ)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return today.replace(hour=16, minute=0, second=0, microsecond=0)

    monkeypatch.setattr(market, "datetime", _FixedDateTime)
    dates = [(today - timedelta(days=i)).date().isoformat() for i in range(5, -1, -1)]
    series = market.Series(
        "AAA", [1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6], [1, 1, 1, 1, 1, 1], dates
    )

    trimmed = market.trim_open_session(series)

    assert len(trimmed.closes) == 6


class _FakeTickResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeTickClient:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls = 0

    async def post(self, *_args, **_kwargs) -> _FakeTickResponse:
        self.calls += 1
        return _FakeTickResponse(self._payload)


async def test_fetch_realtime_tick_returns_scaled_match_price(monkeypatch):
    payload = {"data": {"GetKrxTicksBySymbols": {"ticks": [{"matchPrice": 25.5}]}}}
    fake_client = _FakeTickClient(payload)
    monkeypatch.setattr(market, "client", lambda: fake_client)

    price = await market.fetch_realtime_tick("FPT")

    assert price == 25500
    assert fake_client.calls == 1


async def test_fetch_realtime_tick_returns_none_when_no_ticks_today(monkeypatch):
    payload = {"data": {"GetKrxTicksBySymbols": {"ticks": []}}}
    monkeypatch.setattr(market, "client", lambda: _FakeTickClient(payload))

    assert await market.fetch_realtime_tick("FPT") is None


async def test_fetch_realtime_tick_skips_vnindex(monkeypatch):
    def _boom():
        raise AssertionError("không được gọi tick API cho VNINDEX")

    monkeypatch.setattr(market, "client", _boom)

    assert await market.fetch_realtime_tick("VNINDEX") is None


async def test_current_price_prefers_realtime_tick(monkeypatch):
    async def fake_tick(symbol):
        return 26000.0

    async def fail_fetch(*_args, **_kwargs):
        raise AssertionError("không cần fallback OHLC khi đã có tick realtime")

    monkeypatch.setattr(market, "fetch_realtime_tick", fake_tick)
    monkeypatch.setattr(market, "fetch", fail_fetch)

    price, is_realtime = await market.current_price("FPT")

    assert (price, is_realtime) == (26000.0, True)


async def test_current_price_falls_back_to_ohlc_when_no_tick(monkeypatch):
    async def fake_tick(symbol):
        return None

    async def fake_fetch(symbol, **kwargs):
        return market.Series(symbol, [24000.0], [24000.0], [24000.0], [1000.0], ["2026-08-07"])

    monkeypatch.setattr(market, "fetch_realtime_tick", fake_tick)
    monkeypatch.setattr(market, "fetch", fake_fetch)

    price, is_realtime = await market.current_price("FPT")

    assert (price, is_realtime) == (24000.0, False)
