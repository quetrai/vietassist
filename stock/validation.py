from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


@dataclass(frozen=True)
class DataQuality:
    status: str
    reasons: list[str]
    bars_available: int
    is_stale: bool = False
    has_outlier: bool = False

    @property
    def usable(self) -> bool:
        return self.status != "bad"


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def ohlcv_contract_errors(closes, highs, lows, volumes, dates) -> list[str]:
    if not closes:
        return ["không có dữ liệu giá"]
    lengths = {len(closes), len(highs), len(lows), len(volumes), len(dates)}
    if len(lengths) != 1:
        return ["độ dài OHLCV không đồng nhất"]
    errors = []
    for close, high, low, volume in zip(closes, highs, lows, volumes, strict=True):
        if not all(_finite(value) for value in (close, high, low, volume)):
            errors.append("OHLCV chứa số không hợp lệ")
            break
        if close <= 0 or high <= 0 or low <= 0 or volume < 0 or high < low or not low <= close <= high:
            errors.append("OHLCV vi phạm contract")
            break
    parsed = []
    for date in dates:
        try:
            parsed.append(datetime.strptime(str(date), "%Y-%m-%d").date())
        except ValueError:
            return ["ngày giao dịch không hợp lệ"]
    if len(parsed) != len(set(parsed)) or any(a >= b for a, b in zip(parsed, parsed[1:])):
        errors.append("ngày giao dịch không tăng nghiêm ngặt")
    return errors


def validate_ohlcv(
    closes,
    highs,
    lows,
    volumes,
    dates,
    *,
    min_bars: int = 30,
    max_stale_days: int = 9,
    now: datetime | None = None,
) -> DataQuality:
    errors = ohlcv_contract_errors(closes, highs, lows, volumes, dates)
    if errors:
        return DataQuality("bad", errors, len(closes))
    reasons = []
    stale = False
    outlier = False
    current = now or datetime.now(_VN_TZ)
    last_date = datetime.strptime(dates[-1], "%Y-%m-%d").date()
    age = (current.date() - last_date).days
    if age > max_stale_days:
        stale = True
        reasons.append(f"dữ liệu cũ {age} ngày")
    for previous, current_close in zip(closes, closes[1:]):
        if abs((current_close - previous) / previous * 100) > 35:
            outlier = True
            reasons.append("phát hiện biến động bất thường, cần kiểm tra điều chỉnh giá")
            break
    if len(closes) < 20:
        return DataQuality("bad", ["không đủ 20 phiên dữ liệu"], len(closes), stale, outlier)
    if len(closes) < min_bars:
        reasons.append(f"chỉ có {len(closes)} phiên dữ liệu")
    return DataQuality("degraded" if reasons else "ok", reasons, len(closes), stale, outlier)
