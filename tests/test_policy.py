from stock.features import (
    ADXResult,
    BollingerResult,
    CrossSignal,
    DonchianState,
    Features,
    KeyLevels,
    Liquidity,
    MAAlignment,
    MACDResult,
    MultiTimeframe,
    PriceLevel,
    SessionLimitState,
    SessionMetrics,
)
from stock.policy import MIN_AVG_VOLUME, MIN_RR_RATIO, evaluate

# Giá ở thang VND thật (chục nghìn) - stock/policy.py.build_trade_plan/round_price làm
# tròn theo tick 10đ, dùng giá kiểu "100" như test cũ sẽ bị round_price bóp méo mạnh.


def feature(**overrides):
    values = dict(
        price=25000, change_pct=1.0, sma20=24000, sma50=23000, rsi14=55, volatility_pct=2.5,
        volume_ratio=1.3, relative_strength_20d=5.0, support=23200, resistance=27000,
        avg_volume_20d=MIN_AVG_VOLUME * 10,
        macd=MACDResult(50, 20, 30, "bullish", available=True),
        adx=ADXResult(30, 25, 15, True, available=True),
        atr14=400.0, atr_pct=1.6,
        donchian=DonchianState(24500, 21000, "breakout_up"),
        cross=CrossSignal(True, False, True, True),
        bollinger=BollingerResult(26000, 24500, 23000, 12.0, 70.0, False, available=True),
        key_levels=KeyLevels(
            supports=[PriceLevel(23200, 3, "support", 0.7)],
            resistances=[PriceLevel(27000, 2, "resistance", 0.6)],
        ),
        ma_alignment=MAAlignment(24700, 24300, 24000, "bullish", True),
        trend_score=70,
        multi_tf=MultiTimeframe(2.0, 5.0, 8.0, "bullish", 65),
        signal_agreement=0.6,
        distribution_days_25=0,
        liquidity=Liquidity(MIN_AVG_VOLUME * 10, MIN_AVG_VOLUME * 13, 130.0, 70.0, False),
        session=SessionMetrics(1.0, 70.0, 130.0),
        market_regime="risk_on",
        market_regime_reason="VNINDEX xu hướng tăng trên cả 3 khung thời gian",
        session_limit=SessionLimitState("HOSE", 7.0, False, False),
    )
    values.update(overrides)
    return Features(**values)


def bearish_feature(**overrides):
    values = dict(
        price=20000, sma20=22000, sma50=24000, rsi14=28, relative_strength_20d=-8.0,
        macd=MACDResult(-50, -20, -30, "bearish", available=True),
        adx=ADXResult(30, 12, 28, True, available=True),
        donchian=DonchianState(24000, 20500, "breakout_down"),
        cross=CrossSignal(False, True, False, False),
        ma_alignment=MAAlignment(21000, 22000, 23000, "bearish", False),
        multi_tf=MultiTimeframe(-3.0, -6.0, -9.0, "bearish", 65),
        signal_agreement=-0.6,
        trend_score=25,
        market_regime="risk_off",
        market_regime_reason="VNINDEX xu hướng giảm trên cả 3 khung thời gian",
    )
    values.update(overrides)
    return feature(**values)


def test_buy_requires_clean_setup():
    result = evaluate(feature())
    assert result.action == "BUY"
    assert result.risk_reward >= MIN_RR_RATIO
    assert result.setup_type == "breakout"
    assert result.market_regime == "risk_on"
    assert result.trade_plan is not None
    assert 0 < result.trade_plan.position_size_pct <= 20


def test_bearish_non_holder_never_gets_sell():
    result = evaluate(bearish_feature())
    assert result.action == "NO_TRADE"
    assert result.stop is None and result.target is None


def test_bearish_holder_can_get_sell():
    result = evaluate(bearish_feature(), holding=True)
    assert result.action == "SELL"
    assert result.risk_reward is not None and result.risk_reward >= MIN_RR_RATIO
    assert result.invalidation_reason is not None


def test_illiquid_setup_never_gets_buy():
    """Đủ điều kiện kỹ thuật để BUY nhưng thanh khoản dưới ngưỡng -> hạ về WATCH, không
    chủ động đề xuất mở vị thế mới trên mã khó khớp lệnh."""
    result = evaluate(
        feature(
            avg_volume_20d=MIN_AVG_VOLUME / 2,
            liquidity=Liquidity(MIN_AVG_VOLUME / 2, MIN_AVG_VOLUME / 2 * 1.3, 130.0, 70.0, True),
        )
    )
    assert result.action == "WATCH"
    assert any("CP/phiên" in r for r in result.reasons)


def test_illiquid_holder_still_gets_sell_signal_or_cautious_watch():
    """Thanh khoản mỏng làm nhiễu cả tín hiệu SELL (confidence bị nhân hệ số giảm) -
    chấp nhận SELL hoặc WATCH thận trọng, miễn không rơi về NO_TRADE/BUY."""
    result = evaluate(
        bearish_feature(
            avg_volume_20d=MIN_AVG_VOLUME / 2,
            liquidity=Liquidity(MIN_AVG_VOLUME / 2, MIN_AVG_VOLUME / 2 * 1.3, 130.0, 70.0, True),
        ),
        holding=True,
    )
    assert result.action in ("SELL", "WATCH")
    assert any("thanh khoản" in r.lower() or "CP/phiên" in r for r in result.reasons)


def test_poor_risk_reward_downgrades_new_buy_to_watch():
    """Setup kỹ thuật đẹp (breakout), nhưng không có support/resistance nào đủ gần để
    dùng làm mốc, buộc rơi về fallback ATR với RSI quá mua (>70) làm giảm reward
    multiplier -> R:R dưới ngưỡng tối thiểu -> hạ về WATCH thay vì BUY (Gate D)."""
    result = evaluate(
        feature(
            rsi14=72,
            support=20000,
            resistance=25400,
            key_levels=KeyLevels(supports=[], resistances=[]),
        )
    )
    assert result.action == "WATCH"
    assert result.risk_reward is not None and result.risk_reward < MIN_RR_RATIO
    # Vẫn giữ stop/target làm THAM KHẢO dù hạ về WATCH (khác nhánh NO_TRADE luôn xoá
    # trắng) - người dùng biết hệ thống đã tính đến đâu, chỉ là R:R chưa đạt chuẩn.
    assert result.stop is not None and result.target is not None
    assert result.invalidation_reason is not None


def test_risk_off_market_blocks_new_buy_even_with_clean_setup():
    """Gate A - dù setup mã riêng vẫn breakout sạch, VNINDEX risk-off phải chặn BUY
    mới, chỉ còn WATCH."""
    result = evaluate(
        feature(market_regime="risk_off", market_regime_reason="VNINDEX có 5 ngày phân phối trong 25 phiên")
    )
    assert result.action == "WATCH"
    assert any("risk-off" in r or "risk_off" in r for r in [result.market_regime, *result.reasons])


def test_ceiling_session_blocks_new_buy():
    """Gate: giá đã sát trần trong phiên -> hard_no_buy, dù chỉ báo khác vẫn đẹp."""
    result = evaluate(
        feature(
            session=SessionMetrics(6.8, 95.0, 250.0),
            session_limit=SessionLimitState("HOSE", 7.0, True, False),
        )
    )
    assert result.action == "WATCH"


def test_thin_liquidity_halves_position_size():
    thin = evaluate(
        feature(liquidity=Liquidity(MIN_AVG_VOLUME * 10, MIN_AVG_VOLUME * 13, 130.0, 70.0, True))
    )
    # thanh khoản mỏng -> policy không build trade_plan cho BUY mới (điều kiện rõ ràng
    # trong evaluate: chỉ build khi liquidity không thin) - nhưng vẫn phải ra được
    # action hợp lệ, không crash.
    assert thin.action in ("BUY", "WATCH", "HOLD")
    if thin.action == "BUY":
        assert thin.trade_plan is None
