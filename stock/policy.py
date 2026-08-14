"""Policy layer — nhận `Features` đã tính xong (từ `stock/features.py`), trả về
`Decision` đã chốt hoàn toàn. KHÔNG gọi mạng, KHÔNG tính lại chỉ báo ở đây.

Logic gate (regime/setup/confidence/R:R/position sizing) port từ repo Gemini
(`stock/policy.py`), điều chỉnh để nhận input từ `Features` hợp nhất của
vietassist (xem `stock/features.py`) thay vì `PolicyInputs` rời rạc của bản
gốc. Áp dụng 4 gate:
  Gate A - market regime (đã tính sẵn trong features.market_regime, từ VNINDEX)
  Gate B - chất lượng dữ liệu (features nào thiếu -> tự loại khỏi gate liên quan)
  Gate C - setup quality (breakout/pullback/mean_reversion/none, đồng thuận tín hiệu)
  Gate D - risk/reward (chỉ áp dụng khi cân nhắc BUY/SELL mới)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from stock import features as feat
from stock.features import Features

CONFIDENCE_BUY_MIN = 0.75
CONFIDENCE_WATCH_MIN = 0.55
MIN_RR_RATIO = 1.5
_NEAR_CEILING_STRENGTH_PENALTY = 0.15

# Số CP/phiên trung bình 20 phiên tối thiểu để coi mã là "đủ thanh khoản để hành động".
# Ngưỡng ước lượng thô cho nhà đầu tư cá nhân nhỏ lẻ (không phải chuẩn sàn/CTCK) — chỉ
# để chặn đề xuất BUY trên các mã quá mỏng thanh khoản, nơi lệnh khó khớp đúng vùng giá
# đã tính (stop/target). Khớp với `stock/features.py.calc_liquidity`.
MIN_AVG_VOLUME = 20_000

# B2: sizing theo % NAV
RISK_PER_TRADE_PCT = 1.0   # rủi ro tối đa mỗi lệnh = 1% NAV
MAX_POSITION_PCT = 20.0    # trần tỷ trọng 1 mã
MIN_POSITION_PCT = 2.0

# Ngưỡng session risk
_SELLOFF_PCT = -4.0
_NEAR_FLOOR_MARGIN_PCT = 1.0  # cách sàn <= 1% (theo biên độ sàn niêm yết thật, xem session_limit)
_CLOSE_NEAR_LOW_PCT = 25.0
_DISTRIBUTION_CHANGE_PCT = -2.5
_DISTRIBUTION_VOLUME_RATIO = 150.0


@dataclass(frozen=True)
class TradePlan:
    entry_low: float
    entry_high: float          # vùng mua ±1%, không phải 1 điểm
    target2: float | None      # kháng cự mạnh kế tiếp (phần T1 giữ nguyên field target/stop của Decision)
    position_size_pct: float   # % NAV đề xuất, suy ngược từ khoảng cách stop
    plan_note: str


@dataclass(frozen=True)
class Scenario:
    name: str        # base | bull | bear
    trigger: str     # điều kiện kích hoạt bằng lời, CÓ CON SỐ cụ thể
    action: str


@dataclass(frozen=True)
class Decision:
    action: str              # BUY / HOLD / SELL / WATCH / NO_TRADE
    confidence: float        # 0.0 -> 1.0
    stop: float | None
    target: float | None
    risk_reward: float | None
    reasons: list[str]
    setup_type: str = "none"          # breakout / pullback / mean_reversion / none
    risk_level: str = "medium"        # low / medium / high
    market_regime: str = "unknown"    # risk_on / neutral / risk_off / unknown
    invalidation_reason: str | None = None
    trade_plan: TradePlan | None = None
    scenarios: list[Scenario] = field(default_factory=list)


def build_trade_plan(
    price: float, stop: float, target1: float | None, confidence: float, features: Features,
) -> TradePlan | None:
    """B2 - suy ngược tỷ trọng vị thế từ khoảng cách rủi ro (không bịa số NAV
    thật - đây là % NAV ĐỀ XUẤT dựa trên nguyên tắc rủi ro cố định mỗi lệnh,
    người dùng tự đối chiếu với NAV thật của mình)."""
    if price <= 0 or stop is None or stop >= price or target1 is None:
        return None
    risk_pct = (price - stop) / price * 100
    if risk_pct <= 0:
        return None

    size = RISK_PER_TRADE_PCT / risk_pct * 100
    size *= 0.6 + 0.4 * feat.clamp((confidence - CONFIDENCE_BUY_MIN) / 0.25, 0, 1)
    if features.liquidity and features.liquidity.is_thin:
        size *= 0.5
    size = round(feat.clamp(size, MIN_POSITION_PCT, MAX_POSITION_PCT), 1)

    entry_low = feat.round_price(price * 0.99)
    entry_high = feat.round_price(price * 1.01)

    resistances = features.key_levels.resistances if features.key_levels else []
    target2 = resistances[1].price if len(resistances) > 1 else None

    return TradePlan(
        entry_low=entry_low, entry_high=entry_high, target2=target2, position_size_pct=size,
        plan_note="Chốt 1/2 vị thế tại target, dời stop về hoà vốn cho phần còn lại; "
                   + (f"T2 {target2:,.0f} cho phần còn lại.".replace(",", ".") if target2 else "trail stop theo MA10 cho phần còn lại nếu không có T2 rõ ràng."),
    )


@dataclass
class _SessionFlags:
    is_selloff: bool
    is_near_floor: bool
    is_close_near_low: bool
    is_distribution: bool
    hard_no_buy: bool
    reasons: list[str]


def _evaluate_session(features: Features) -> _SessionFlags:
    """Nhận diện phiên rủi ro cao để chặn BUY sai. Với cổ phiếu Việt Nam,
    volume bùng nổ trong phiên giảm mạnh/đóng sát đáy thường là phân phối
    hoặc force-sell, không phải tín hiệu gom hàng."""
    session = features.session
    reasons: list[str] = []
    if session is None:
        return _SessionFlags(False, False, False, False, False, reasons)

    limit = features.session_limit
    limit_pct = limit.limit_pct if limit else 7.0
    is_near_floor = session.daily_change_pct <= -(limit_pct - _NEAR_FLOOR_MARGIN_PCT)
    is_selloff = session.daily_change_pct <= _SELLOFF_PCT
    is_close_near_low = session.close_position_pct <= _CLOSE_NEAR_LOW_PCT and session.daily_change_pct < 0
    is_distribution = session.daily_change_pct <= _DISTRIBUTION_CHANGE_PCT and session.volume_ratio_pct >= _DISTRIBUTION_VOLUME_RATIO

    if is_near_floor:
        reasons.append(f"giảm rất mạnh {session.daily_change_pct:.2f}% trong phiên (sát biên độ sàn {limit_pct:.0f}%)")
    elif is_selloff:
        reasons.append(f"giảm mạnh {session.daily_change_pct:.2f}% trong phiên")
    if is_distribution:
        reasons.append(f"volume {session.volume_ratio_pct:.1f}% so với TB20 trong phiên giảm — dấu hiệu phân phối")
    if is_close_near_low:
        reasons.append(f"đóng cửa sát đáy phiên (vị trí close {session.close_position_pct:.1f}% biên độ ngày)")
    if limit and limit.at_ceiling:
        reasons.append(f"giá đã sát trần ({limit_pct:.0f}% — sàn {limit.exchange or 'không rõ'}) — mua đuổi dễ kẹp hàng vì không có bên bán đối ứng")

    below_both_sma = bool(features.cross and not features.cross.above_sma20 and not features.cross.above_sma50)
    macd_bad = bool(features.macd and features.macd.available and (features.macd.crossover == "bearish" or features.macd.histogram < 0))

    hard_no_buy = (
        is_near_floor
        or (is_selloff and is_distribution)
        or (is_distribution and is_close_near_low)
        or (below_both_sma and macd_bad)
        or bool(limit and limit.at_ceiling)
    )
    if below_both_sma and macd_bad and not hard_no_buy:
        reasons.append("giá dưới cả SMA20/SMA50 và MACD bearish")

    return _SessionFlags(is_selloff, is_near_floor, is_close_near_low, is_distribution, hard_no_buy, reasons)


@dataclass
class _Bias:
    direction: str  # bullish / bearish / conflict / flat
    bull_votes: int
    bear_votes: int


def _classify_bias(features: Features) -> _Bias:
    ma = features.ma_alignment
    agreement = features.signal_agreement or 0.0
    trend_3m = features.multi_tf.trend_3m if features.multi_tf else 0.0

    bull_votes = sum([
        trend_3m > 3,
        bool(ma and ma.alignment == "bullish"),
        features.relative_strength_20d > 3,
        agreement > 0.3,
    ])
    bear_votes = sum([
        trend_3m < -3,
        bool(ma and ma.alignment == "bearish"),
        features.relative_strength_20d < -3,
        agreement < -0.3,
    ])

    if bull_votes >= 2 and bear_votes >= 2:
        return _Bias("conflict", bull_votes, bear_votes)
    if bull_votes > bear_votes and bull_votes >= 1:
        return _Bias("bullish", bull_votes, bear_votes)
    if bear_votes > bull_votes and bear_votes >= 1:
        return _Bias("bearish", bull_votes, bear_votes)
    return _Bias("flat", bull_votes, bear_votes)


def _detect_setup(features: Features, bias: _Bias, session_flags: _SessionFlags) -> tuple[str, float, list[str]]:
    """Gate C - chỉ công nhận setup 'sạch' nếu đa số điều kiện của setup đó
    khớp. Trả (setup_type, setup_strength 0..1, reasons)."""
    ma = features.ma_alignment
    rsi = features.rsi14
    reasons: list[str] = []

    if features.donchian is None or ma is None or ma.alignment == "unknown":
        return "none", 0.3, ["chưa đủ dữ liệu để xác định setup rõ ràng"]

    volume_confirm = bool(features.liquidity and features.liquidity.liquidity_ratio_pct >= 120)

    if features.donchian.state == "breakout_up" and not session_flags.hard_no_buy:
        strength = 0.6
        if volume_confirm:
            strength += 0.2
            reasons.append("breakout khỏi kênh Donchian 20 phiên có volume xác nhận")
        else:
            reasons.append("breakout khỏi kênh Donchian 20 phiên nhưng volume chưa xác nhận rõ")
        if bias.direction == "bullish":
            strength += 0.1
        if features.session is not None and features.session_limit and features.session.daily_change_pct > features.session_limit.limit_pct - _NEAR_CEILING_STRENGTH_PENALTY * 10:
            strength -= _NEAR_CEILING_STRENGTH_PENALTY
            reasons.append("mua đuổi phiên tăng sát trần: hàng về T+2.5, nếu breakout fail sẽ không kịp thoát")
        return "breakout", feat.clamp(strength, 0.0, 1.0), reasons

    if features.donchian.state == "breakout_down":
        reasons.append("breakdown xuống dưới kênh Donchian 20 phiên")
        return "breakdown", 0.6 if bias.direction == "bearish" else 0.4, reasons

    if (
        ma.alignment == "bullish" and bias.direction != "bearish"
        and rsi < 68 and (features.multi_tf and features.multi_tf.trend_3m > 0)
    ):
        strength = 0.55
        if features.relative_strength_20d > 0:
            strength += 0.1
            reasons.append("outperform VNINDEX, MA alignment bullish - setup pullback-to-trend")
        else:
            reasons.append("MA alignment bullish, chưa rõ outperform VNINDEX")
        return "pullback", min(strength, 1.0), reasons

    if features.bollinger and features.bollinger.available and (features.bollinger.pct_b < 10 or rsi < 30):
        if features.relative_strength_20d > -10 and not session_flags.is_near_floor:
            reasons.append("giá về sát dải Bollinger dưới / RSI vùng quá bán - mean reversion có kiểm soát")
            return "mean_reversion", 0.5, reasons
        reasons.append("giá quá bán nhưng relative strength quá yếu / gần sàn - không coi là mean reversion an toàn")
        return "none", 0.3, reasons

    reasons.append("không khớp mẫu setup rõ ràng nào (breakout/pullback/mean-reversion)")
    return "none", 0.35, reasons


def _compute_confidence(features: Features, bias: _Bias, setup_strength: float, regime: str) -> float:
    """Confidence có nghĩa vận hành (xem CONFIDENCE_*_MIN). Blend setup
    quality + đồng thuận tín hiệu + trend score, trừ điểm khi regime xấu hoặc
    thanh khoản mỏng."""
    agreement = features.signal_agreement or 0.0
    trend_score = features.trend_score
    # agreement/trend_score đều đo mức đồng thuận TĂNG - bias bearish thì đảo
    # (1 - x) để đo đúng mức đồng thuận GIẢM, nếu không SELL gần như không
    # bao giờ đạt ngưỡng dù tín hiệu giảm rất rõ.
    if bias.direction == "bearish":
        agreement_component = (1 - agreement) / 2
        trend_component = 1 - (trend_score / 100) if trend_score is not None else 0.5
    else:
        agreement_component = (agreement + 1) / 2
        trend_component = (trend_score / 100) if trend_score is not None else 0.5

    confidence = setup_strength * 0.40 + agreement_component * 0.30 + trend_component * 0.30

    if regime == "risk_off" and bias.direction == "bullish":
        confidence -= 0.15
    elif regime == "risk_on" and bias.direction == "bullish":
        confidence += 0.05
    elif regime == "risk_off" and bias.direction == "bearish":
        confidence += 0.05

    if features.liquidity and features.liquidity.is_thin:
        confidence *= 0.85

    return round(feat.clamp(confidence, 0.0, 1.0), 2)


def _compute_stop_target(
    features: Features, direction: str,
) -> tuple[float | None, float | None, float | None, str]:
    """Gate D input - stop/target ưu tiên ATR thật; chỉ rơi về % biến động
    lịch sử khi không có H/L thật để tính ATR. stop/target ưu tiên đặt tại
    support/resistance THẬT (swing pivot) khi mốc đó nằm trong biên độ rủi ro
    hợp lý — chỉ khi không có S/R dùng được mới fallback về khoảng cách theo
    ATR/% biến động.

    direction: "buy" (cân nhắc mua mới) | "exit" (SELL - tham khảo chốt
    lời/cắt lỗ nếu đang giữ) | "watch" (chưa đủ rõ để đề xuất vùng giá).
    """
    price = features.price
    if price <= 0 or direction == "watch":
        return None, None, None, "watch"

    atr = features.atr14
    if atr is not None and atr > 0:
        risk_amount = feat.clamp(atr * 1.5, price * 0.01, price * 0.10)
        basis = "atr"
    else:
        risk_pct = feat.clamp(features.volatility_pct, 3, 8)
        risk_amount = price * risk_pct / 100
        basis = "volatility_pct"

    rsi = features.rsi14
    rsi_adj = -0.3 if rsi > 70 else (0.3 if rsi < 30 else 0.0)
    support = features.support
    resistance = features.resistance
    trend_3m = features.multi_tf.trend_3m if features.multi_tf else 0.0

    if direction == "buy":
        reward_mult = feat.clamp(1.5 + rsi_adj, 0.8, 2.5) if trend_3m >= 0 else 1.0

        if support is not None and 0 < support < price and (price - support) <= risk_amount * 2:
            stop = feat.round_price(support * 0.99)
            basis = f"{basis}+support"
        else:
            stop = feat.round_price(price - risk_amount)
        if stop >= price:
            stop -= 10
        risk = price - stop
        if risk <= 0:
            return None, None, None, basis

        if resistance is not None and resistance > price and (resistance - price) >= risk:
            target = feat.round_price(resistance)
            basis = f"{basis}+resistance"
        else:
            target = feat.round_price(price + risk * reward_mult)

        rr = round((target - price) / risk, 2)
        return stop, target, rr, basis

    # exit: invalidation ở TRÊN giá, target tham khảo ở DƯỚI giá.
    reward_mult = feat.clamp(1.5 + rsi_adj, 0.8, 2.5) if trend_3m <= 0 else 1.0

    if resistance is not None and resistance > price and (resistance - price) <= risk_amount * 2:
        invalidation = feat.round_price(resistance * 1.01)
        basis = f"{basis}+resistance"
    else:
        invalidation = feat.round_price(price + risk_amount)
    if invalidation <= price:
        invalidation += 10
    risk = invalidation - price
    if risk <= 0:
        return None, None, None, basis

    if support is not None and support < price and (price - support) >= risk:
        target = feat.round_price(support)
        basis = f"{basis}+support"
    else:
        target = feat.round_price(price - risk * reward_mult)

    rr = round((price - target) / risk, 2)
    return invalidation, target, rr, basis


def _risk_level(features: Features, session_flags: _SessionFlags) -> str:
    atr_pct = features.atr_pct
    if atr_pct is not None:
        base = "high" if atr_pct > 6 else ("medium" if atr_pct > 3 else "low")
    else:
        base = "high" if features.volatility_pct > 15 else ("medium" if features.volatility_pct > 8 else "low")
    if session_flags.hard_no_buy:
        return "high" if base != "low" else "medium"
    return base


def _build_scenarios(stop: float, target: float, plan: TradePlan | None) -> list[Scenario]:
    """B3 - 3 kịch bản deterministic dựa hoàn toàn trên số của stop/target đã
    chốt, KHÔNG suy diễn thêm số mới."""
    entry_low = plan.entry_low if plan else stop
    entry_high = plan.entry_high if plan else stop
    base = Scenario(
        name="base",
        trigger=f"Giá giữ trên vùng stop {stop:,.0f}, tích lũy đi ngang trong vùng {entry_low:,.0f}-{entry_high:,.0f}".replace(",", "."),
        action=f"Nắm giữ theo kế hoạch, chờ chạm target {target:,.0f} để chốt 1/2 vị thế".replace(",", "."),
    )
    target2 = plan.target2 if plan else None
    if target2 is not None:
        bull_action = f"Giữ phần còn lại nhắm T2 {target2:,.0f}, dời stop về hoà vốn (entry ~{entry_low:,.0f})".replace(",", ".")
    else:
        bull_action = "Không có T2 rõ ràng - trail stop theo MA10 cho phần còn lại thay vì chốt cứng"
    bull = Scenario(
        name="bull",
        trigger=f"Đóng cửa vượt target {target:,.0f} kèm volume > 130% trung bình 20 phiên".replace(",", "."),
        action=bull_action,
    )
    bear = Scenario(
        name="bear",
        trigger=f"Đóng cửa dưới stop {stop:,.0f}".replace(",", "."),
        action="Cắt toàn bộ vị thế theo stop đã định, không bình quân giá xuống",
    )
    return [base, bull, bear]


def evaluate(features: Features, *, holding: bool = False) -> Decision:
    regime = features.market_regime
    session_flags = _evaluate_session(features)
    bias = _classify_bias(features)
    setup_type, setup_strength, setup_reasons = _detect_setup(features, bias, session_flags)
    confidence = _compute_confidence(features, bias, setup_strength, regime)
    risk_level = _risk_level(features, session_flags)

    reasons: list[str] = [
        f"Giá {'trên' if features.price > features.sma20 else 'dưới'} SMA20",
        f"RSI14 {features.rsi14:.1f}",
        f"Sức mạnh tương đối 20 phiên {features.relative_strength_20d:+.2f}%",
        f"Thanh khoản {features.volume_ratio:.2f} lần trung bình 20 phiên",
    ]
    if regime == "risk_off":
        reasons.append(features.market_regime_reason)
    reasons.extend(setup_reasons)
    reasons.extend(session_flags.reasons)
    if features.liquidity and features.liquidity.is_thin:
        reasons.append(
            f"Khối lượng bình quân 20 phiên ~{features.avg_volume_20d:,.0f} CP/phiên — "
            f"dưới ngưỡng {MIN_AVG_VOLUME:,.0f}, lệnh có thể khó khớp đúng vùng giá tính toán".replace(",", ".")
        )

    action = "NO_TRADE"
    direction = "watch"
    invalidation_reason = None

    if bias.direction == "bullish":
        can_buy = confidence >= CONFIDENCE_BUY_MIN and regime != "risk_off" and not session_flags.hard_no_buy
        if can_buy:
            direction = "buy"
        elif confidence >= CONFIDENCE_WATCH_MIN:
            action = "HOLD" if holding else "WATCH"
            if regime == "risk_off":
                reasons.append("VNINDEX đang risk-off - hạn chế mở mua mới dù setup mã riêng còn ổn")
            if session_flags.hard_no_buy:
                reasons.append("phiên gần nhất có rủi ro phân phối/breakdown/kịch trần - chưa mở mua mới")
        else:
            action = "HOLD" if holding else "NO_TRADE"
            reasons.append("confidence chưa đạt ngưỡng để hành động, tín hiệu tăng còn yếu")
    elif bias.direction == "bearish":
        if confidence >= CONFIDENCE_BUY_MIN:
            if holding:
                action, direction = "SELL", "exit"
            else:
                action = "NO_TRADE"
                reasons.append("tín hiệu giảm rõ - không phải cơ hội mua mới")
        elif confidence >= CONFIDENCE_WATCH_MIN:
            if holding:
                action, direction = "WATCH", "exit"
                reasons.append("tín hiệu giảm đang hình thành - theo dõi sát, chưa đủ mạnh để cắt ngay")
            else:
                action = "NO_TRADE"
                reasons.append("tín hiệu giảm chưa đủ rõ để kết luận - không mua mới")
        else:
            action = "HOLD" if holding else "NO_TRADE"
            reasons.append("confidence chưa đủ cao để khẳng định tín hiệu giảm")
    elif bias.direction == "conflict":
        reasons.append("tín hiệu tăng/giảm mâu thuẫn nhau - chưa đủ rõ ràng để hành động")
        action = ("HOLD" if holding else "WATCH") if confidence >= CONFIDENCE_WATCH_MIN else ("HOLD" if holding else "NO_TRADE")
    else:
        action = "HOLD" if holding else "NO_TRADE"
        reasons.append(
            "chưa có tín hiệu rõ ràng theo hướng nào - giữ nguyên vị thế, theo dõi thêm" if holding
            else "chưa có tín hiệu rõ ràng theo hướng nào - chưa có cơ sở để mở vị thế mới"
        )

    stop = target = rr = None
    trade_plan: TradePlan | None = None
    scenarios: list[Scenario] = []

    if direction == "buy":
        stop, target, rr, _basis = _compute_stop_target(features, "buy")
        if stop is None or rr is None:
            action = "HOLD" if holding else "WATCH"
            reasons.append("không tính được stop/target hợp lệ - không đề xuất vùng giá")
        elif rr < MIN_RR_RATIO:
            action = "HOLD" if holding else "WATCH"
            reasons.append(f"risk/reward {rr} dưới ngưỡng tối thiểu {MIN_RR_RATIO} - chưa {'mở thêm' if holding else 'vào mới'} dù setup ổn")
            invalidation_reason = f"chờ R:R cải thiện (hiện {rr}, cần >= {MIN_RR_RATIO})"
        else:
            action = "HOLD" if holding else "BUY"
            invalidation_reason = (
                f"nếu giá đóng cửa dưới {stop:,.0f} nên cân nhắc cắt lỗ phần đang giữ" if holding
                else f"nếu giá đóng cửa dưới {stop:,.0f} coi như setup thất bại, cần cắt lỗ"
            ).replace(",", ".")
            if not holding and features.liquidity and not features.liquidity.is_thin:
                trade_plan = build_trade_plan(features.price, stop, target, confidence, features)
    elif direction == "exit":
        stop, target, rr, _basis = _compute_stop_target(features, "exit")
        if stop is None or rr is None:
            action = "HOLD" if holding else "WATCH"
            reasons.append("không tính được stop/target hợp lệ - không đề xuất vùng giá")
        else:
            invalidation_reason = f"nếu giá vượt lên trên {stop:,.0f} thì tín hiệu SELL coi như vô hiệu, cần đánh giá lại".replace(",", ".")

    if action == "NO_TRADE":
        stop = target = rr = None
        trade_plan = None
        if not reasons:
            reasons.append("edge không đủ rõ để ra quyết định - ưu tiên đứng ngoài")

    if action in ("BUY", "HOLD") and direction == "buy" and stop is not None and target is not None:
        scenarios = _build_scenarios(stop, target, trade_plan)

    return Decision(
        action=action,
        confidence=confidence,
        stop=stop,
        target=target,
        risk_reward=rr,
        reasons=reasons,
        setup_type=setup_type,
        risk_level=risk_level,
        market_regime=regime,
        invalidation_reason=invalidation_reason,
        trade_plan=trade_plan,
        scenarios=scenarios,
    )
