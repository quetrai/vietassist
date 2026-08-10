from stock.features import Features
from stock.policy import MAX_STOP_RISK_PCT, MIN_AVG_VOLUME, evaluate


def feature(**overrides):
    values = {
        "price": 100,
        "change_pct": 1,
        "sma20": 95,
        "sma50": 90,
        "rsi14": 55,
        "volatility_pct": 2,
        "volume_ratio": 1.3,
        "relative_strength_20d": 5,
        "support": 92,
        "resistance": 115,
        "avg_volume_20d": MIN_AVG_VOLUME * 10,  # thanh khoản dồi dào theo mặc định
    }
    values.update(overrides)
    return Features(**values)


def test_buy_requires_clean_setup():
    result = evaluate(feature())
    assert result.action == "BUY"
    assert result.risk_reward >= 1.5


def test_bearish_non_holder_never_gets_sell():
    result = evaluate(feature(price=80, sma20=90, sma50=100, rsi14=30, relative_strength_20d=-8))
    assert result.action == "NO_TRADE"


def test_bearish_holder_can_get_sell():
    result = evaluate(
        feature(price=80, sma20=90, sma50=100, rsi14=30, relative_strength_20d=-8),
        holding=True,
    )
    assert result.action == "SELL"


def test_illiquid_setup_never_gets_buy():
    """Đủ điều kiện kỹ thuật để BUY nhưng thanh khoản dưới ngưỡng -> hạ về WATCH, không
    chủ động đề xuất mở vị thế mới trên mã khó khớp lệnh."""
    result = evaluate(feature(avg_volume_20d=MIN_AVG_VOLUME / 2))
    assert result.action == "WATCH"
    assert any(
        "Thanh khoản" not in r and "thanh khoản" in r.lower() for r in result.reasons
    ) or any("CP/phiên" in r for r in result.reasons)


def test_illiquid_holder_still_gets_sell_signal_if_bearish():
    result = evaluate(
        feature(
            price=80,
            sma20=90,
            sma50=100,
            rsi14=30,
            relative_strength_20d=-8,
            avg_volume_20d=MIN_AVG_VOLUME / 2,
        ),
        holding=True,
    )
    assert result.action == "SELL"


def test_wide_stop_downgrades_new_buy_to_watch():
    """Setup kỹ thuật đẹp, thanh khoản tốt, nhưng support cách giá quá xa khiến stop
    vượt ngưỡng rủi ro tối đa cho 1 lệnh mới -> hạ về WATCH thay vì BUY."""
    result = evaluate(feature(support=100 * (1 - (MAX_STOP_RISK_PCT + 5) / 100)))
    assert result.action == "WATCH"
    assert result.stop is not None


def test_wide_stop_still_allows_sell_for_existing_holder():
    result = evaluate(
        feature(
            price=80,
            sma20=90,
            sma50=100,
            rsi14=30,
            relative_strength_20d=-8,
        ),
        holding=True,
    )
    assert result.action == "SELL"
