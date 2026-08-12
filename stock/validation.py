"""Cổng chất lượng dữ liệu OHLCV — port từ repo Gemini (`stock/validation.py`).

Vì sao cần thêm lớp này: `stock/market.py.validate()` đã có sẵn nhưng chỉ kiểu
ALL-OR-NOTHING — raise lỗi cứng nếu vi phạm contract cơ bản (độ dài lệch,
low<=close<=high...), không có gì ở giữa. Nó KHÔNG phát hiện được dữ liệu vẫn
hợp lệ về mặt cấu trúc nhưng đáng ngờ về chất lượng: quá cũ (phiên gần nhất
cách đây nhiều ngày — có thể do fetch lỗi âm thầm trả về cache cũ), biến động
1 phiên bất thường (dấu hiệu corporate action chưa điều chỉnh, xem
`stock/price_adjust.py`), hoặc chỉ có vài phiên (chỉ báo tính ra không đáng
tin). Module này thêm trạng thái thứ 3 — "degraded": vẫn phân tích được,
nhưng phải nói rõ lý do độ tin cậy giảm, thay vì im lặng trình bày như dữ liệu
sạch 100%.

`stock/market.py.validate()` vẫn giữ nguyên vai trò chặn cứng ở tầng fetch
(dữ liệu vi phạm contract không bao giờ lọt tới đây) — hàm ở đây chạy THÊM một
lớp đánh giá mềm hơn sau khi fetch đã thành công.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
_OUTLIER_DAILY_MOVE_PCT = 35.0
MIN_BARS_HARD_FLOOR = 20
DEFAULT_MIN_BARS = 30
DEFAULT_MAX_STALE_CALENDAR_DAYS = 9


@dataclass
class DataQuality:
    status: str  # "ok" | "degraded" | "bad"
    reasons: list[str] = field(default_factory=list)
    bars_available: int = 0
    is_stale: bool = False
    has_outlier: bool = False
    has_duplicate_dates: bool = False
    has_length_mismatch: bool = False
    has_invalid_numbers: bool = False
    has_ohlc_violation: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def usable(self) -> bool:
        """False = KHÔNG được dùng để tính chỉ báo/ra quyết định (dưới sàn tối
        thiểu hoặc vi phạm contract). True bao gồm cả "degraded" — vẫn phân
        tích được, chỉ cần nêu rõ hạn chế."""
        return self.status != "bad"


def _parse_date(value: object) -> object:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _finite(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(float(value))


def ohlcv_contract_errors(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    dates: list[str] | None,
) -> list[str]:
    if not closes:
        return ["không có dữ liệu giá"]
    n = len(closes)
    errors: list[str] = []
    lengths = {"close": n, "high": len(highs), "low": len(lows), "volume": len(volumes)}
    if len(set(lengths.values())) != 1:
        errors.append("độ dài OHLCV không đồng nhất: " + ", ".join(f"{k}={v}" for k, v in lengths.items()))
    if dates and len(dates) != n:
        errors.append(f"độ dài ngày không khớp: date={len(dates)}, close={n}")
    if errors:
        return errors
    invalid = broken = 0
    for close, high, low, volume in zip(closes, highs, lows, volumes, strict=True):
        if not all(_finite(v) for v in (close, high, low, volume)) or close <= 0 or high <= 0 or low <= 0 or volume < 0:
            invalid += 1
            continue
        if high < low or not low <= close <= high:
            broken += 1
    if invalid:
        errors.append(f"có {invalid} bar chứa NaN/inf/giá không dương hoặc volume âm")
    if broken:
        errors.append(f"có {broken} bar vi phạm low <= close <= high")
    if dates:
        parsed = [_parse_date(d) for d in dates]
        if any(d is None for d in parsed):
            errors.append("ngày phải đúng định dạng YYYY-MM-DD")
        valid = [d for d in parsed if d is not None]
        if len(valid) != len(set(valid)):
            errors.append("phát hiện ngày trùng lặp trong chuỗi giá")
        if any(valid[i] >= valid[i + 1] for i in range(len(valid) - 1)):
            errors.append("chuỗi ngày phải tăng nghiêm ngặt")
    return errors


def validate_ohlcv(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    dates: list[str] | None,
    *,
    min_bars: int = DEFAULT_MIN_BARS,
    max_stale_days: int = DEFAULT_MAX_STALE_CALENDAR_DAYS,
    now: datetime | None = None,
) -> DataQuality:
    n = len(closes)
    errors = ohlcv_contract_errors(closes, highs, lows, volumes, dates)
    if errors:
        joined = " ".join(errors)
        return DataQuality(
            "bad", errors, n,
            has_duplicate_dates="trùng lặp" in joined,
            has_length_mismatch="độ dài" in joined,
            has_invalid_numbers="NaN/inf" in joined,
            has_ohlc_violation="low <= close <= high" in joined,
        )
    reasons: list[str] = []
    stale = outlier = False
    if dates:
        parsed_last = _parse_date(dates[-1])
        if parsed_last is not None:
            age = ((now or datetime.now(_VN_TZ)).date() - parsed_last).days
            if age > max_stale_days:
                stale = True
                reasons.append(f"dữ liệu cũ - phiên gần nhất cách đây {age} ngày")
    for previous, current in zip(closes, closes[1:], strict=False):
        if not previous:
            continue
        move = abs((current - previous) / previous * 100)
        if move > _OUTLIER_DAILY_MOVE_PCT:
            outlier = True
            reasons.append(f"biến động bất thường {move:.1f}% - cần kiểm tra corporate action/điều chỉnh giá")
            break
    if n < MIN_BARS_HARD_FLOOR:
        reasons.append(f"chỉ có {n} phiên - dưới ngưỡng tối thiểu {MIN_BARS_HARD_FLOOR} để tính chỉ báo")
        return DataQuality("bad", reasons, n, stale, outlier)
    if n < min_bars:
        reasons.append(f"chỉ có {n} phiên - dưới mức khuyến nghị {min_bars} để tin cậy cao")
    return DataQuality("degraded" if reasons else "ok", reasons, n, stale, outlier)
