from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from stock import validation

_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _dates(n: int, end: datetime) -> list[str]:
    return [(end - timedelta(days=n - 1 - i)).strftime("%Y-%m-%d") for i in range(n)]


def _flat_series(n: int, end: datetime, base: float = 100.0) -> tuple[list[float], list[float], list[float], list[float], list[str]]:
    closes = [base + i * 0.01 for i in range(n)]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    volumes = [1000.0] * n
    return closes, highs, lows, volumes, _dates(n, end)


def test_ok_status_for_clean_recent_data():
    now = datetime(2026, 8, 11, tzinfo=_VN_TZ)
    closes, highs, lows, volumes, dates = _flat_series(60, now)
    quality = validation.validate_ohlcv(closes, highs, lows, volumes, dates, now=now)

    assert quality.status == "ok"
    assert quality.reasons == []
    assert quality.usable is True


def test_bad_status_for_length_mismatch():
    quality = validation.validate_ohlcv([1, 2, 3], [1, 2], [1, 2, 3], [1, 2, 3], None)

    assert quality.status == "bad"
    assert quality.has_length_mismatch is True
    assert quality.usable is False


def test_bad_status_for_ohlc_violation():
    # low=5 > close=2, vi phạm low <= close <= high.
    quality = validation.validate_ohlcv([2.0] * 25, [10.0] * 25, [5.0] * 25, [1000.0] * 25, None)

    assert quality.status == "bad"
    assert quality.has_ohlc_violation is True


def test_bad_status_for_duplicate_or_non_increasing_dates():
    now = datetime(2026, 8, 11, tzinfo=_VN_TZ)
    closes, highs, lows, volumes, dates = _flat_series(25, now)
    dates[-1] = dates[-2]  # trùng ngày

    quality = validation.validate_ohlcv(closes, highs, lows, volumes, dates, now=now)

    assert quality.status == "bad"
    assert quality.has_duplicate_dates is True


def test_bad_status_below_hard_floor_bars():
    now = datetime(2026, 8, 11, tzinfo=_VN_TZ)
    closes, highs, lows, volumes, dates = _flat_series(10, now)  # < MIN_BARS_HARD_FLOOR (20)

    quality = validation.validate_ohlcv(closes, highs, lows, volumes, dates, now=now)

    assert quality.status == "bad"
    assert quality.usable is False


def test_degraded_status_for_stale_data():
    now = datetime(2026, 8, 11, tzinfo=_VN_TZ)
    stale_end = now - timedelta(days=30)
    closes, highs, lows, volumes, dates = _flat_series(60, stale_end)

    quality = validation.validate_ohlcv(closes, highs, lows, volumes, dates, now=now)

    assert quality.status == "degraded"
    assert quality.is_stale is True
    assert quality.usable is True


def test_degraded_status_for_outlier_move():
    now = datetime(2026, 8, 11, tzinfo=_VN_TZ)
    closes, highs, lows, volumes, dates = _flat_series(60, now)
    closes[-1] = closes[-2] * 1.5  # +50% trong 1 phiên, vượt ngưỡng outlier (35%)
    highs[-1] = closes[-1] + 1
    lows[-1] = closes[-1] - 1

    quality = validation.validate_ohlcv(closes, highs, lows, volumes, dates, now=now)

    assert quality.status == "degraded"
    assert quality.has_outlier is True


def test_degraded_status_for_fewer_bars_than_recommended():
    now = datetime(2026, 8, 11, tzinfo=_VN_TZ)
    # 25 bar: trên hard floor (20) nhưng dưới khuyến nghị (30).
    closes, highs, lows, volumes, dates = _flat_series(25, now)

    quality = validation.validate_ohlcv(closes, highs, lows, volumes, dates, now=now)

    assert quality.status == "degraded"
    assert quality.bars_available == 25


def test_ohlcv_contract_errors_empty_for_clean_data():
    now = datetime(2026, 8, 11, tzinfo=_VN_TZ)
    closes, highs, lows, volumes, dates = _flat_series(30, now)
    assert validation.ohlcv_contract_errors(closes, highs, lows, volumes, dates) == []
