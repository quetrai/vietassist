from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PriceGap:
    date: str
    move_pct: float
    level: str


@dataclass(frozen=True)
class PriceAudit:
    is_adjusted: bool | None
    gaps: list[PriceGap]
    note: str


def detect_price_gaps(closes, dates=None, suspect_pct: float = 12.0, certain_pct: float = 25.0) -> list[PriceGap]:
    gaps = []
    for index, (previous, current) in enumerate(zip(closes, closes[1:]), start=1):
        if previous <= 0 or current <= 0:
            continue
        move_pct = (current - previous) / previous * 100
        if abs(move_pct) < suspect_pct:
            continue
        gaps.append(
            PriceGap(
                dates[index] if dates and index < len(dates) else "",
                round(move_pct, 2),
                "certain" if abs(move_pct) >= certain_pct else "suspect",
            )
        )
    return gaps


def audit_series(symbol: str, source: str, closes, dates=None) -> PriceAudit:
    gaps = detect_price_gaps(closes, dates)
    certain = any(gap.level == "certain" for gap in gaps)
    if not gaps:
        return PriceAudit(None, [], "")
    note = (
        f"Chuỗi giá {symbol} từ {source} có gap lớn bất thường; cần thận trọng với "
        "SMA, hỗ trợ, kháng cự và ATR."
    )
    if certain:
        note = (
            f"Chuỗi giá {symbol} từ {source} có gap rất lớn, có khả năng liên quan "
            "điều chỉnh giá hoặc corporate action; không dùng các mốc trước gap để "
            "kết luận mạnh."
        )
    return PriceAudit(False if certain else None, gaps, note)
