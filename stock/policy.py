from __future__ import annotations

from dataclasses import dataclass

from stock.features import Features

# Số CP/phiên trung bình 20 phiên tối thiểu để coi mã là "đủ thanh khoản để hành động".
# Đây là ngưỡng ước lượng thô cho nhà đầu tư cá nhân nhỏ lẻ (không phải chuẩn của sàn
# hay CTCK) — chỉ để chặn việc đề xuất BUY trên các mã quá mỏng thanh khoản, nơi lệnh
# khó khớp đúng vùng giá đã tính (stop/target).
MIN_AVG_VOLUME = 20_000

# Nếu khoảng cách từ giá hiện tại tới stop vượt ngưỡng này (%), không tự tin đề xuất
# BUY nữa dù R:R vẫn đạt — vì phần trăm lỗ tối đa nếu giá chạm stop đã khá lớn cho một
# lệnh mới mở. Đặt ở 12% (không phải 7-8%) vì biên độ dao động 1 phiên của HOSE đã là
# ±7%, nên stop dựa trên support 20 phiên hợp lý thường rộng hơn 1 phiên trần/sàn — đặt
# quá chặt sẽ hạ cả những setup bình thường xuống WATCH một cách vô ích. Hạ xuống WATCH
# kèm lý do thay vì chặn hẳn, để người dùng tự cân nhắc thay vì bot quyết thay.
MAX_STOP_RISK_PCT = 12.0


@dataclass(frozen=True)
class Decision:
    action: str
    confidence: float
    stop: float | None
    target: float | None
    risk_reward: float | None
    reasons: list[str]


def evaluate(features: Features, *, holding: bool = False) -> Decision:
    bullish = sum(
        [
            features.price > features.sma20,
            features.sma20 > features.sma50,
            45 <= features.rsi14 <= 68,
            features.relative_strength_20d > 0,
            features.volume_ratio >= 1.1,
        ]
    )
    bearish = sum(
        [
            features.price < features.sma20,
            features.sma20 < features.sma50,
            features.rsi14 < 40,
            features.relative_strength_20d < -3,
        ]
    )
    reasons = [
        f"Giá {'trên' if features.price > features.sma20 else 'dưới'} SMA20",
        f"RSI14 {features.rsi14:.1f}",
        f"Sức mạnh tương đối 20 phiên {features.relative_strength_20d:+.2f}%",
        f"Thanh khoản {features.volume_ratio:.2f} lần trung bình 20 phiên",
    ]
    illiquid = features.avg_volume_20d < MIN_AVG_VOLUME
    if illiquid:
        reasons = [
            *reasons,
            f"Khối lượng bình quân 20 phiên ~{features.avg_volume_20d:,.0f} CP/phiên — "
            f"dưới ngưỡng {MIN_AVG_VOLUME:,.0f}, lệnh có thể khó khớp đúng vùng giá tính toán",
        ]

    if bearish >= 3:
        return Decision(
            "SELL" if holding else "NO_TRADE",
            min(0.95, 0.5 + bearish * 0.1),
            None,
            None,
            None,
            reasons,
        )
    if bullish < 4:
        return Decision(
            "HOLD" if holding else "WATCH", 0.45 + bullish * 0.07, None, None, None, reasons
        )
    if illiquid and not holding:
        # Đủ điều kiện kỹ thuật để mua nhưng thanh khoản quá thấp — không chủ động đề
        # xuất mở vị thế mới trên mã khó vào/ra lệnh; người đang giữ mã (holding=True)
        # vẫn được đánh giá tiếp bên dưới vì thanh khoản thấp không phải lý do để bỏ
        # qua tín hiệu bán một mã đang xấu.
        return Decision("WATCH", 0.5, None, None, None, reasons)
    stop = min(features.support * 0.99, features.price * 0.95)
    risk = features.price - stop
    risk_pct = (risk / features.price * 100) if features.price else 0.0
    target = max(features.resistance, features.price + risk * 1.5)
    rr = (target - features.price) / risk if risk > 0 else 0
    if rr < 1.5:
        return Decision(
            "HOLD" if holding else "WATCH", 0.7, stop, target, rr, [*reasons, "R:R dưới 1.5"]
        )
    if risk_pct > MAX_STOP_RISK_PCT and not holding:
        return Decision(
            "WATCH",
            0.6,
            stop,
            target,
            rr,
            [
                *reasons,
                f"Stop cách giá {risk_pct:.1f}% — vượt ngưỡng rủi ro {MAX_STOP_RISK_PCT:.0f}%/lệnh, "
                "chưa đủ an toàn để mở vị thế mới",
            ],
        )
    return Decision(
        "HOLD" if holding else "BUY", min(0.95, 0.55 + bullish * 0.08), stop, target, rr, reasons
    )
