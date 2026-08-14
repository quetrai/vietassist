"""Feature layer: chỉ tính toán chỉ báo/tín hiệu trên OHLCV, KHÔNG kết luận
BUY/SELL/NO_TRADE — mọi ngưỡng diễn giải thuộc về stock/policy.py.

Phần lớn các hàm chỉ báo kỹ thuật (MACD/ADX/ATR/Donchian/swing-pivot S/R/MA
alignment/thanh khoản percentile/distribution days...) được port gần như
nguyên văn từ repo Gemini (`stock/features.py`) — đã có unit test kỹ ở đó,
kèm chú thích cẩn thận về việc KHÔNG bịa số khi thiếu dữ liệu thật (vd
không suy ADX/ATR từ H/L giả lập, không suy Bollinger từ %price khi lịch sử
quá ngắn). Phần dưới cùng (`Features`/`calculate`) là lớp facade riêng của
vietassist, gộp mọi chỉ báo + market regime (từ VNINDEX) vào MỘT object duy
nhất để `stock/policy.py` và `stock/analysis.py` dùng, thay vì phải truyền
rời rạc nhiều object nhỏ như bản Gemini gốc.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from stock.market import Series


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def round_price(v: float) -> float:
    return round(v / 10) * 10


# ─── RSI (Wilder) ────────────────────────────────────────────────────────────

def calc_rsi(closes: list[float], period: int = 14) -> float | None:
    """Trả None khi chưa đủ dữ liệu - KHÔNG bịa giá trị 50 (trung tính) trông
    như một chỉ báo thật, vì nó sẽ chảy vào scoring như thể có dữ liệu."""
    if len(closes) < period + 1:
        return None
    avg_gain = avg_loss = 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            avg_gain += diff
        else:
            avg_loss += abs(diff)
    avg_gain /= period
    avg_loss /= period
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = diff if diff >= 0 else 0
        loss = abs(diff) if diff < 0 else 0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


# Giữ hàm `rsi()` cũ (raise thay vì trả None) làm alias tương thích ngược cho
# test/call site nào còn phụ thuộc hành vi cũ.
def rsi(values: list[float], period: int = 14) -> float:
    if len(values) < period + 1:
        raise ValueError("Không đủ dữ liệu RSI")
    result = calc_rsi(values, period)
    return result if result is not None else 50.0


# ─── Momentum (hồi quy tuyến tính) ───────────────────────────────────────────

def calc_momentum_slope(closes: list[float], period: int = 10) -> float:
    s = closes[-period:]
    n = len(s)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2
    y_mean = sum(s) / n
    num = sum((i - x_mean) * (s[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return (num / den / s[-1]) * 100 if den and s[-1] else 0.0


# ─── SMA / EMA ───────────────────────────────────────────────────────────────

def calc_sma(closes: list[float], period: int) -> float:
    if len(closes) < period:
        return closes[-1] if closes else 0.0
    sl = closes[-period:]
    return sum(sl) / period


def sma(values: list[float], period: int) -> float:
    """Bản strict (raise khi thiếu dữ liệu) - dùng cho test/call site cần biết
    chắc chắn có đủ lịch sử, khác `calc_sma` (permissive, fallback an toàn)."""
    if len(values) < period:
        raise ValueError("Không đủ dữ liệu SMA")
    return sum(values[-period:]) / period


def _ema_series(closes: list[float], period: int) -> list[float]:
    if not closes:
        return []
    alpha = 2 / (period + 1)
    if len(closes) < period:
        seed = sum(closes) / len(closes)
        return [seed] * len(closes)
    result = [0.0] * len(closes)
    ema = sum(closes[:period]) / period
    for i in range(period):
        result[i] = ema
    for i in range(period, len(closes)):
        ema = closes[i] * alpha + ema * (1 - alpha)
        result[i] = ema
    return result


def calc_ema(closes: list[float], period: int) -> float:
    series = _ema_series(closes, period)
    return series[-1] if series else 0.0


def pct_change(values: list[float], periods: int) -> float:
    if len(values) <= periods:
        return 0.0
    start, end = values[-periods - 1], values[-1]
    return (end / start - 1) * 100 if start else 0.0


# ─── MACD(12,26,9) ───────────────────────────────────────────────────────────

@dataclass
class MACDResult:
    macd_line: float = 0.0
    signal_line: float = 0.0
    histogram: float = 0.0
    crossover: str = "none"  # bullish | bearish | none
    available: bool = False


def calc_macd(closes: list[float]) -> MACDResult:
    # Cần >= 35 phiên để signal_line là EMA9 THẬT trên chuỗi MACD (không rơi
    # vào nhánh seed trung bình phẳng của _ema_series) - dưới 35 phiên,
    # histogram bị méo nhưng vẫn "available" như tín hiệu thật nếu không chặn ở
    # đây, dễ chảy sai vào các phép tính đồng thuận tín hiệu phía sau.
    if len(closes) < 35:
        return MACDResult()
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    macd_series = [a - b for a, b in zip(ema12, ema26, strict=True)]
    valid_macd = macd_series[26:]
    signal_series = _ema_series(valid_macd, 9)

    macd_line = macd_series[-1] if macd_series else 0.0
    signal_line = signal_series[-1] if signal_series else 0.0
    histogram = macd_line - signal_line

    crossover = "none"
    if len(signal_series) >= 2:
        prev_hist = macd_series[-2] - signal_series[-2]
        if prev_hist < 0 and histogram > 0:
            crossover = "bullish"
        if prev_hist > 0 and histogram < 0:
            crossover = "bearish"

    return MACDResult(
        round(macd_line, 2), round(signal_line, 2), round(histogram, 2), crossover, available=True
    )


# ─── Bollinger Bands(20, 2σ) ─────────────────────────────────────────────────

@dataclass
class BollingerResult:
    upper: float
    middle: float
    lower: float
    width: float
    pct_b: float
    squeeze: bool
    available: bool = True


def calc_bollinger(closes: list[float], price: float) -> BollingerResult:
    """Khi lịch sử ngắn hơn period, KHÔNG bịa dải theo %price - trả về
    available=False để tầng policy biết đây không phải dữ liệu thật."""
    period = 20
    if len(closes) < period:
        return BollingerResult(price, price, price, 0.0, 50.0, False, available=False)
    sl = closes[-period:]
    middle = sum(sl) / period
    variance = sum((v - middle) ** 2 for v in sl) / period
    std_dev = variance ** 0.5
    upper = middle + 2 * std_dev
    lower = middle - 2 * std_dev
    width = ((upper - lower) / middle) * 100 if middle > 0 else 5.0
    pct_b = ((price - lower) / (upper - lower)) * 100 if upper != lower else 50.0
    return BollingerResult(
        round(upper), round(middle), round(lower), round(width, 2),
        round(clamp(pct_b, 0, 100), 1), width < 5, available=True,
    )


# ─── Multi-timeframe trend ───────────────────────────────────────────────────

@dataclass
class MultiTimeframe:
    trend_1w: float
    trend_1m: float
    trend_3m: float
    alignment: str  # bullish | bearish | mixed
    bars_used_3m: int = 0  # số phiên thật sự dùng để tính trend_3m (< 65 nếu dữ liệu ngắn)


def calc_multi_timeframe(closes: list[float]) -> MultiTimeframe:
    def pct(n: int) -> float:
        if len(closes) < n + 1:
            return 0.0
        past = closes[-1 - n]
        curr = closes[-1]
        return ((curr - past) / past) * 100 if past > 0 else 0.0

    bars_3m = min(len(closes) - 1, 65) if closes else 0
    t1w, t1m, t3m = pct(5), pct(22), pct(bars_3m)
    vals = [t1w, t1m, t3m]
    if all(v > 0 for v in vals):
        alignment = "bullish"
    elif all(v < 0 for v in vals):
        alignment = "bearish"
    else:
        alignment = "mixed"
    return MultiTimeframe(round(t1w, 2), round(t1m, 2), round(t3m, 2), alignment, bars_used_3m=max(bars_3m, 0))


# ─── SMA cross (golden/death) ────────────────────────────────────────────────

@dataclass
class CrossSignal:
    golden_cross: bool
    death_cross: bool
    above_sma20: bool
    above_sma50: bool


def calc_cross_signal(closes: list[float]) -> CrossSignal:
    price = closes[-1] if closes else 0.0
    if len(closes) < 52:
        return CrossSignal(False, False, False, False)
    sma20 = calc_sma(closes, 20)
    sma50 = calc_sma(closes, 50)
    window = 3
    golden = death = False
    for i in range(len(closes) - window, len(closes) - 1):
        s20p = calc_sma(closes[: i + 1], 20)
        s50p = calc_sma(closes[: i + 1], 50)
        s20c = calc_sma(closes[: i + 2], 20)
        s50c = calc_sma(closes[: i + 2], 50)
        if s20p <= s50p and s20c > s50c:
            golden = True
        if s20p >= s50p and s20c < s50c:
            death = True
    return CrossSignal(golden, death, price > sma20, price > sma50)


# ─── ADX(14) ──────────────────────────────────────────────────────────────────

@dataclass
class ADXResult:
    adx: float
    di_plus: float
    di_minus: float
    trending: bool
    available: bool = True


def _wilder_true_range_series(
    closes: list[float], highs: list[float], lows: list[float]
) -> tuple[list[float], list[float], list[float]]:
    trs, dm_plus, dm_minus = [], [], []
    for i in range(1, len(closes)):
        high, low, prev_close = highs[i], lows[i], closes[i - 1]
        prev_high, prev_low = highs[i - 1], lows[i - 1]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        up_move = high - prev_high
        down_move = prev_low - low
        dm_plus.append(up_move if (up_move > down_move and up_move > 0) else 0)
        dm_minus.append(down_move if (down_move > up_move and down_move > 0) else 0)
    return trs, dm_plus, dm_minus


def _has_real_hl(closes: list[float], highs: list[float] | None, lows: list[float] | None) -> bool:
    """Nguồn dữ liệu fallback đôi khi gán high = low = close cho phiên thiếu
    H/L thật - chỉ check độ dài như trước sẽ để lọt toàn bộ chuỗi H/L giả (mọi
    phiên high == low) vào ADX/ATR, trông như chỉ báo thật trong khi thực chất
    tính trên số bịa. Một vài phiên đứng giá thật sự có thể có high == low,
    nhưng nếu ĐA SỐ phiên đều vậy thì gần như chắc chắn là H/L giả toàn chuỗi."""
    if not highs or not lows or len(highs) != len(closes) or len(lows) != len(closes):
        return False
    bars_with_real_range = sum(1 for h, l in zip(highs, lows, strict=True) if h > l)
    return bars_with_real_range >= len(closes) * 0.5


def calc_adx(closes: list[float], highs: list[float] | None = None, lows: list[float] | None = None, period: int = 14) -> ADXResult:
    """Chỉ tính ADX khi có high/low THẬT (không tự tổng hợp H/L từ close - số
    bịa trông như chỉ báo thật sẽ chảy vào scoring một cách âm thầm). Nếu
    thiếu dữ liệu -> available=False, tầng policy phải loại ADX khỏi mọi gate
    dựa vào nó."""
    has_real = _has_real_hl(closes, highs, lows)
    if len(closes) < period * 2 or not has_real:
        return ADXResult(0.0, 0.0, 0.0, False, available=False)

    trs, dm_plus, dm_minus = _wilder_true_range_series(closes, highs, lows)

    def smooth(arr: list[float]) -> list[float]:
        res = [0.0] * len(arr)
        if len(arr) < period:
            return res
        res[period - 1] = sum(arr[:period])
        for i in range(period, len(arr)):
            res[i] = res[i - 1] - res[i - 1] / period + arr[i]
        return res

    atr = smooth(trs)
    sdm_plus = smooth(dm_plus)
    sdm_minus = smooth(dm_minus)

    di_plus_series = [(v / atr[i] * 100) if atr[i] > 0 else 0 for i, v in enumerate(sdm_plus)]
    di_minus_series = [(v / atr[i] * 100) if atr[i] > 0 else 0 for i, v in enumerate(sdm_minus)]
    dx_series = []
    for i, v in enumerate(di_plus_series):
        s = v + di_minus_series[i]
        dx_series.append((abs(v - di_minus_series[i]) / s * 100) if s > 0 else 0)

    valid_dx = dx_series[period - 1:]
    if len(valid_dx) < period:
        return ADXResult(0.0, 0.0, 0.0, False, available=False)
    adx = sum(valid_dx[-period:]) / period
    di_plus = di_plus_series[-1] if di_plus_series else 0.0
    di_minus = di_minus_series[-1] if di_minus_series else 0.0

    return ADXResult(round(adx, 1), round(di_plus, 1), round(di_minus, 1), adx > 25, available=True)


# ─── ATR(14) - dùng cho stop/target theo biến động thật, không phải %price ──

def calc_atr(closes: list[float], highs: list[float] | None, lows: list[float] | None, period: int = 14) -> float | None:
    """Average True Range (Wilder). Trả None nếu thiếu H/L thật hoặc chưa đủ
    dữ liệu - KHÔNG suy ra ATR xấp xỉ từ %price vì nó che giấu việc thiếu dữ
    liệu thật, giống lý do calc_adx từ chối bịa H/L."""
    if not _has_real_hl(closes, highs, lows) or len(closes) < period + 1:
        return None
    trs, _, _ = _wilder_true_range_series(closes, highs, lows)
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
    return round(atr, 2)


# ─── Donchian breakout state ─────────────────────────────────────────────────

@dataclass
class DonchianState:
    upper: float | None
    lower: float | None
    state: str  # breakout_up | breakout_down | inside | unknown


def calc_donchian_breakout(highs: list[float], lows: list[float], closes: list[float], period: int = 20) -> DonchianState:
    """So giá đóng cửa hiện tại với kênh Donchian ĐƯỢC TÍNH TRƯỚC phiên hiện
    tại (loại bar cuối ra khỏi kênh) - nếu tính cả bar cuối, breakout sẽ
    không bao giờ xảy ra vì bar cuối luôn nằm trong chính kênh của nó."""
    if len(highs) < period + 1 or len(lows) < period + 1 or not closes:
        return DonchianState(None, None, "unknown")
    prior_highs = highs[-period - 1:-1]
    prior_lows = lows[-period - 1:-1]
    upper, lower = max(prior_highs), min(prior_lows)
    price = closes[-1]
    if price > upper:
        state = "breakout_up"
    elif price < lower:
        state = "breakout_down"
    else:
        state = "inside"
    return DonchianState(round(upper), round(lower), state)


# ─── Support/Resistance thô (min/max) - giữ làm fallback khi swing pivot
#     (find_key_levels bên dưới) không tìm được cụm nào ─────────────────────

@dataclass
class SupportResistance:
    support: float | None
    resistance: float | None
    dist_to_support: float
    dist_to_resistance: float


def calc_support_resistance(highs: list[float], lows: list[float], price: float, lookback: int = 30) -> SupportResistance:
    length = min(len(highs), len(lows), lookback)
    if length == 0:
        return SupportResistance(None, None, 0.0, 0.0)
    recent_highs = highs[-length:]
    recent_lows = lows[-length:]
    resistance = max(recent_highs)
    support = min(recent_lows)
    dist_support = round((price - support) / support * 100, 1) if support > 0 else 0.0
    dist_resistance = round((price - resistance) / resistance * 100, 1) if resistance > 0 else 0.0
    return SupportResistance(round(support), round(resistance), dist_support, dist_resistance)


# ─── S/R theo swing pivot + clustering (mức giá thị trường THẬT sự "tôn
#     trọng", đếm được số lần test - khác min/max thô 20-30 phiên ở trên) ────

@dataclass
class PriceLevel:
    price: float
    touches: int        # số lần giá test vùng này
    kind: str            # support | resistance
    strength: float      # 0..1: kết hợp touches + độ gần giá hiện tại


@dataclass
class KeyLevels:
    supports: list[PriceLevel] = field(default_factory=list)     # sort gần giá nhất trước
    resistances: list[PriceLevel] = field(default_factory=list)  # sort gần giá nhất trước


def _find_swing_pivots(highs: list[float], lows: list[float], window: int) -> tuple[list[float], list[float]]:
    """Swing high tại i khi highs[i] == max(highs[i-w:i+w+1]); swing low
    tương tự. KHÔNG dùng bar cuối cùng (chưa xác nhận - cần w phiên sau đó
    để biết đó thực sự là đỉnh/đáy cục bộ)."""
    n = len(highs)
    swing_highs, swing_lows = [], []
    for i in range(window, n - window):
        h_window = highs[i - window: i + window + 1]
        l_window = lows[i - window: i + window + 1]
        if highs[i] == max(h_window):
            swing_highs.append(highs[i])
        if lows[i] == min(l_window):
            swing_lows.append(lows[i])
    return swing_highs, swing_lows


def _cluster_pivots(pivots: list[float], cluster_pct: float) -> list[tuple[float, int]]:
    """Gom cụm các pivot cách nhau <= cluster_pct%: giá cụm = trung bình có
    trọng số theo số lần test, touches cộng dồn. Trả list (giá_cụm, touches)."""
    if not pivots:
        return []
    clusters: list[list[float]] = []
    for p in sorted(pivots):
        placed = False
        for cluster in clusters:
            cluster_avg = sum(cluster) / len(cluster)
            if cluster_avg > 0 and abs(p - cluster_avg) / cluster_avg * 100 <= cluster_pct:
                cluster.append(p)
                placed = True
                break
        if not placed:
            clusters.append([p])
    return [(sum(c) / len(c), len(c)) for c in clusters]


def find_key_levels(
    highs: list[float], lows: list[float], closes: list[float],
    lookback: int = 60, pivot_window: int = 3, cluster_pct: float = 1.5,
) -> KeyLevels:
    min_bars = pivot_window * 2 + 2
    if len(highs) < min_bars or len(lows) < min_bars or not closes:
        return KeyLevels([], [])

    length = min(len(highs), len(lows), lookback)
    w_highs = highs[-length:]
    w_lows = lows[-length:]
    price = closes[-1]

    swing_highs, swing_lows = _find_swing_pivots(w_highs, w_lows, pivot_window)
    high_clusters = _cluster_pivots(swing_highs, cluster_pct)
    low_clusters = _cluster_pivots(swing_lows, cluster_pct)

    def _to_level(cluster_price: float, touches: int, kind: str) -> PriceLevel:
        proximity = clamp(1 - abs(price - cluster_price) / price / 0.15, 0, 1) if price > 0 else 0.0
        strength = clamp(touches / 4, 0, 1) * 0.6 + proximity * 0.4
        return PriceLevel(round_price(cluster_price), touches, kind, round(strength, 2))

    supports = [_to_level(p, t, "support") for p, t in low_clusters if p < price]
    resistances = [_to_level(p, t, "resistance") for p, t in high_clusters if p > price]

    supports.sort(key=lambda lv: price - lv.price)
    resistances.sort(key=lambda lv: lv.price - price)

    return KeyLevels(supports=supports, resistances=resistances)


# ─── MA alignment + trend score ──────────────────────────────────────────────

@dataclass
class MAAlignment:
    ma5: float
    ma10: float
    ma20: float
    alignment: str  # bullish | bearish | mixed | unknown
    is_bullish: bool | None


def calc_ma_alignment(closes: list[float]) -> MAAlignment:
    if len(closes) < 20:
        return MAAlignment(0, 0, 0, "unknown", None)
    ma5, ma10, ma20 = calc_sma(closes, 5), calc_sma(closes, 10), calc_sma(closes, 20)
    if ma5 > ma10 > ma20:
        alignment, is_bullish = "bullish", True
    elif ma5 < ma10 < ma20:
        alignment, is_bullish = "bearish", False
    else:
        alignment, is_bullish = "mixed", None
    return MAAlignment(round(ma5), round(ma10), round(ma20), alignment, is_bullish)


def calc_trend_score(ma_align: MAAlignment, rsi14: float | None, macd_histogram: float) -> int:
    """Điểm mô tả 0-100 (càng cao càng thiên tăng) - đây là FEATURE mô tả
    trạng thái trend, không phải quyết định action; tầng policy tự diễn giải
    ngưỡng nào là 'đủ tốt' cho từng gate."""
    score = 50
    if ma_align.alignment == "bullish":
        score += 20
    elif ma_align.alignment == "bearish":
        score -= 20
    elif ma_align.ma5 > ma_align.ma10 or ma_align.ma10 > ma_align.ma20:
        score += 5
    else:
        score -= 5

    if rsi14 is not None:
        if rsi14 > 70:
            score -= 8
        elif rsi14 < 30:
            score += 8
        elif rsi14 > 55:
            score += 5
        elif rsi14 < 45:
            score -= 5

    score += 5 if macd_histogram > 0 else -5
    return int(max(0, min(100, round(score))))


# ─── Thanh khoản: volume hiện tại vs trung bình 20 phiên + percentile ───────

@dataclass
class Liquidity:
    avg_volume_20: float
    current_volume: float
    liquidity_ratio_pct: float  # current vs avg20, 100 = bằng trung bình
    volume_percentile: float  # 0-100, percentile của volume hiện tại trong lịch sử gần đây
    is_thin: bool  # thanh khoản quá thấp - khuyến nghị mua/bán gần như vô nghĩa vì khó vào/ra


def _percentile_rank(current: float, history: list[float]) -> float:
    if not history:
        return 50.0
    below_or_equal = sum(1 for h in history if h <= current)
    return round(below_or_equal / len(history) * 100, 1)


def calc_liquidity(volumes: list[float], min_avg_volume: float = 20_000, percentile_lookback: int = 60) -> Liquidity | None:
    """So khối lượng khớp phiên gần nhất với trung bình 20 phiên + percentile
    trong `percentile_lookback` phiên gần nhất. Đây là cảnh báo broker luôn
    nêu đầu tiên: mã thanh khoản thấp thì "mua/bán" gần như vô nghĩa vì không
    đủ đối ứng để vào/ra ở khối lượng đáng kể. `min_avg_volume` khớp
    `stock/policy.py.MIN_AVG_VOLUME` - ngưỡng ước lượng thô cho nhà đầu tư cá
    nhân nhỏ lẻ, không phải chuẩn của sàn/CTCK."""
    if not volumes:
        return None
    window = volumes[-20:] if len(volumes) >= 20 else volumes
    if not window:
        return None
    avg20 = sum(window) / len(window)
    current = volumes[-1]
    ratio = (current / avg20) * 100 if avg20 > 0 else 0.0
    history = volumes[-percentile_lookback - 1:-1] if len(volumes) > 1 else []
    percentile = _percentile_rank(current, history)
    return Liquidity(
        avg_volume_20=round(avg20),
        current_volume=round(current),
        liquidity_ratio_pct=round(ratio, 1),
        volume_percentile=percentile,
        is_thin=avg20 < min_avg_volume,
    )


def calc_distribution_days(closes: list[float], volumes: list[float], lookback: int = 25) -> int:
    """Đếm 'ngày phân phối' chuẩn O'Neil trong `lookback` phiên gần nhất: 1
    phiên giảm > 0.2% kèm volume cao hơn phiên liền trước = 1 ngày phân phối
    - tín hiệu tổ chức lớn đang bán ra dù giá chưa xác nhận downtrend rõ. Đây
    là FEATURE thuần đếm số liệu; ngưỡng bao nhiêu ngày thì coi là xấu thuộc
    về policy."""
    n = min(len(closes), len(volumes))
    if n < 2:
        return 0
    c, v = closes[-n:], volumes[-n:]
    window_start = max(1, len(c) - lookback)
    count = 0
    for i in range(window_start, len(c)):
        if c[i - 1] <= 0:
            continue
        change_pct = (c[i] - c[i - 1]) / c[i - 1] * 100
        if change_pct <= -0.2 and v[i] > v[i - 1]:
            count += 1
    return count


# ─── Session metrics (phiên gần nhất) ────────────────────────────────────────

@dataclass
class SessionMetrics:
    daily_change_pct: float
    close_position_pct: float  # 0 = đóng sát đáy phiên, 100 = đóng sát đỉnh phiên
    volume_ratio_pct: float  # volume phiên gần nhất vs TB20 phiên trước đó


def calc_session_metrics(closes: list[float], highs: list[float], lows: list[float], volumes: list[float]) -> SessionMetrics | None:
    if len(closes) < 2:
        return None
    price, prev_close = closes[-1], closes[-2]
    daily_change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0.0

    high = highs[-1] if highs else price
    low = lows[-1] if lows else price
    close_position_pct = (price - low) / (high - low) * 100 if high > low else 50.0

    volume_ratio_pct = 100.0
    if volumes:
        prior = volumes[-21:-1] if len(volumes) >= 21 else volumes[:-1]
        if prior:
            avg20 = sum(prior) / len(prior)
            volume_ratio_pct = volumes[-1] / avg20 * 100 if avg20 > 0 else 100.0

    return SessionMetrics(
        daily_change_pct=round(daily_change_pct, 2),
        close_position_pct=round(close_position_pct, 1),
        volume_ratio_pct=round(volume_ratio_pct, 1),
    )


# ─── Biên độ trần/sàn theo sàn niêm yết (đặc thù thị trường VN) ─────────────

PRICE_LIMIT_PCT: dict[str, float] = {
    "HOSE": 7.0,
    "HSX": 7.0,
    "HNX": 10.0,
    "UPCOM": 15.0,
}
_DEFAULT_PRICE_LIMIT_PCT = 7.0  # không rõ sàn -> giả định chặt nhất (HOSE) cho an toàn


@dataclass
class SessionLimitState:
    exchange: str | None
    limit_pct: float
    at_ceiling: bool     # đóng cửa sát trần (trong 0.3% biên độ trần)
    at_floor: bool        # đóng cửa sát sàn


def calc_session_limit_state(
    daily_change_pct: float | None, exchange: str | None, tolerance_pct: float = 0.3,
) -> SessionLimitState:
    """Cảnh báo khi giá đã kịch trần/sàn - kỹ thuật thường "gãy" quy luật
    thông thường trong tình huống này (biên độ mỗi sàn khác nhau: HOSE ±7%,
    HNX ±10%, UPCOM ±15%), và mua đuổi giá trần dễ kẹp hàng vì không có bên
    bán đối ứng."""
    limit_pct = PRICE_LIMIT_PCT.get((exchange or "").strip().upper(), _DEFAULT_PRICE_LIMIT_PCT)
    if daily_change_pct is None:
        return SessionLimitState(exchange, limit_pct, False, False)
    at_ceiling = daily_change_pct >= limit_pct - tolerance_pct
    at_floor = daily_change_pct <= -(limit_pct - tolerance_pct)
    return SessionLimitState(exchange, limit_pct, at_ceiling, at_floor)


# ─── Enhanced indicators bundle + signal agreement ──────────────────────────

@dataclass
class EnhancedIndicators:
    macd: MACDResult
    bollinger: BollingerResult
    multi_tf: MultiTimeframe
    cross: CrossSignal
    adx: ADXResult
    donchian: DonchianState
    sma20: float
    sma50: float
    ema9: float
    atr14: float | None
    atr_pct: float | None  # ATR như % giá - dùng để so sánh biến động giữa các mã


def build_enhanced_indicators(closes: list[float], price: float, highs: list[float] | None = None, lows: list[float] | None = None) -> EnhancedIndicators:
    atr14 = calc_atr(closes, highs, lows)
    atr_pct = round(atr14 / price * 100, 2) if atr14 is not None and price > 0 else None
    return EnhancedIndicators(
        macd=calc_macd(closes),
        bollinger=calc_bollinger(closes, price),
        multi_tf=calc_multi_timeframe(closes),
        cross=calc_cross_signal(closes),
        adx=calc_adx(closes, highs, lows),
        donchian=calc_donchian_breakout(highs or [], lows or [], closes),
        sma20=round(calc_sma(closes, 20)),
        sma50=round(calc_sma(closes, 50)),
        ema9=round(calc_ema(closes, 9)),
        atr14=atr14,
        atr_pct=atr_pct,
    )


def calc_signal_agreement(ind: EnhancedIndicators) -> float:
    """Điểm đồng thuận -1..1 giữa các tín hiệu kỹ thuật hiện có (chỉ đếm tín
    hiệu THẬT SỰ available). +1 = mọi tín hiệu đồng thuận tăng, -1 = đồng
    thuận giảm, gần 0 = tín hiệu mâu thuẫn."""
    votes: list[float] = []

    if ind.macd.available:
        if ind.macd.crossover == "bullish":
            votes.append(1.0)
        elif ind.macd.crossover == "bearish":
            votes.append(-1.0)
        elif ind.macd.histogram != 0:
            votes.append(1.0 if ind.macd.histogram > 0 else -1.0)

    if ind.multi_tf.alignment == "bullish":
        votes.append(1.0)
    elif ind.multi_tf.alignment == "bearish":
        votes.append(-1.0)

    if ind.cross.golden_cross:
        votes.append(1.0)
    if ind.cross.death_cross:
        votes.append(-1.0)
    if not ind.cross.golden_cross and not ind.cross.death_cross:
        votes.append(1.0 if (ind.cross.above_sma20 and ind.cross.above_sma50) else (-1.0 if not ind.cross.above_sma20 and not ind.cross.above_sma50 else 0.0))

    if ind.adx.available and ind.adx.trending:
        votes.append(1.0 if ind.adx.di_plus > ind.adx.di_minus else -1.0)

    if ind.donchian.state == "breakout_up":
        votes.append(1.0)
    elif ind.donchian.state == "breakout_down":
        votes.append(-1.0)

    if not votes:
        return 0.0
    return round(sum(votes) / len(votes), 2)


# ─── Market regime (VNINDEX) ─────────────────────────────────────────────────

@dataclass
class MarketRegime:
    regime: str  # risk_on | neutral | risk_off | unknown
    vnindex_alignment: str
    vnindex_distribution_days: int
    reason: str


DISTRIBUTION_DAY_THRESHOLD = 4  # >= 4 ngày phân phối / 25 phiên -> ép risk_off (chuẩn O'Neil)


def classify_market_regime(vnindex: Series) -> MarketRegime:
    """Gate thị trường chung: risk_on/neutral/risk_off dựa trên xu hướng
    VNINDEX. Bảo thủ theo hướng risk_off - chỉ cần MỘT trong các tín hiệu sau
    (trend 3 khung thời gian bearish, ADX xác nhận trending xuống, hoặc >= 4
    ngày phân phối/25 phiên) là đủ để coi thị trường chung xấu và hạn chế BUY
    mới, vì cái giá của một BUY sai trong thị trường xấu thường nặng hơn cái
    giá bỏ lỡ một BUY đúng."""
    closes, highs, lows, volumes = vnindex.closes, vnindex.highs, vnindex.lows, vnindex.volumes
    multi_tf = calc_multi_timeframe(closes)
    adx = calc_adx(closes, highs, lows)
    distribution_days = calc_distribution_days(closes, volumes)

    if distribution_days >= DISTRIBUTION_DAY_THRESHOLD:
        return MarketRegime(
            "risk_off", multi_tf.alignment, distribution_days,
            f"VNINDEX có {distribution_days} ngày phân phối trong 25 phiên gần nhất - dấu hiệu tổ "
            "chức lớn bán ra, ép thị trường chung về risk-off",
        )
    if multi_tf.alignment == "bearish":
        return MarketRegime("risk_off", multi_tf.alignment, distribution_days, "VNINDEX xu hướng giảm trên cả 3 khung thời gian (1 tuần/1 tháng/3 tháng)")
    if adx.available and adx.trending and adx.di_minus > adx.di_plus:
        return MarketRegime("risk_off", multi_tf.alignment, distribution_days, f"VNINDEX ADX={adx.adx} xác nhận xu hướng giảm đang mạnh (-DI {adx.di_minus} > +DI {adx.di_plus})")
    if multi_tf.alignment == "bullish":
        return MarketRegime("risk_on", multi_tf.alignment, distribution_days, "VNINDEX xu hướng tăng trên cả 3 khung thời gian (1 tuần/1 tháng/3 tháng)")
    return MarketRegime("neutral", multi_tf.alignment, distribution_days, "VNINDEX chưa có xu hướng rõ ràng (đi ngang/mixed)")


# ─── Facade: Features tổng hợp + calculate() ────────────────────────────────

@dataclass(frozen=True)
class Features:
    price: float
    change_pct: float
    sma20: float
    sma50: float
    rsi14: float
    volatility_pct: float
    volume_ratio: float
    relative_strength_20d: float
    support: float
    resistance: float
    avg_volume_20d: float = 0.0
    # ── Nhóm 1: kỹ thuật nâng cao ──
    macd: MACDResult | None = None
    adx: ADXResult | None = None
    atr14: float | None = None
    atr_pct: float | None = None
    donchian: DonchianState | None = None
    cross: CrossSignal | None = None
    bollinger: BollingerResult | None = None
    key_levels: KeyLevels | None = None
    ma_alignment: MAAlignment | None = None
    trend_score: int | None = None
    multi_tf: MultiTimeframe | None = None
    signal_agreement: float | None = None
    distribution_days_25: int = 0
    liquidity: Liquidity | None = None
    session: SessionMetrics | None = None
    # ── Nhóm 2: bối cảnh thị trường chung ──
    market_regime: str = "unknown"
    market_regime_reason: str = ""
    # ── Nhóm 5: đặc thù sàn VN ──
    session_limit: SessionLimitState | None = None


def calculate(series: Series, market: Series, *, exchange: str | None = None) -> Features:
    closes, highs, lows, volumes = series.closes, series.highs, series.lows, series.volumes
    price = series.price

    returns = [
        (b / a - 1) * 100
        for a, b in zip(closes[-21:-1], closes[-20:], strict=True)
        if a
    ]
    mean = sum(returns) / len(returns) if returns else 0.0
    variance = sum((value - mean) ** 2 for value in returns) / max(1, len(returns) - 1) if returns else 0.0
    avg_volume = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else (sum(volumes) / len(volumes) if volumes else 0.0)
    own_20d = pct_change(closes, 20)
    market_20d = pct_change(market.closes, 20)

    enhanced = build_enhanced_indicators(closes, price, highs, lows)
    ma_align = calc_ma_alignment(closes)
    rsi14 = calc_rsi(closes)
    trend_score = calc_trend_score(ma_align, rsi14, enhanced.macd.histogram) if ma_align.alignment != "unknown" else None
    session = calc_session_metrics(closes, highs, lows, volumes)
    regime = classify_market_regime(market)
    limit_state = calc_session_limit_state(session.daily_change_pct if session else None, exchange)

    sr_raw = calc_support_resistance(highs, lows, price)
    key_levels = find_key_levels(highs, lows, closes)
    # Ưu tiên swing-pivot clustering (mức giá thị trường thật sự "tôn trọng");
    # fallback về min/max thô 20-30 phiên khi chưa tìm được cụm pivot nào
    # (lịch sử quá ngắn hoặc thị trường chưa tạo đỉnh/đáy rõ).
    support = key_levels.supports[0].price if key_levels.supports else sr_raw.support
    resistance = key_levels.resistances[0].price if key_levels.resistances else sr_raw.resistance

    return Features(
        price=price,
        change_pct=pct_change(closes, 1),
        sma20=enhanced.sma20,
        sma50=enhanced.sma50,
        rsi14=round(rsi14, 1) if rsi14 is not None else 50.0,
        volatility_pct=variance ** 0.5,
        volume_ratio=volumes[-1] / avg_volume if avg_volume else 0.0,
        relative_strength_20d=own_20d - market_20d,
        support=support if support is not None else price,
        resistance=resistance if resistance is not None else price,
        avg_volume_20d=avg_volume,
        macd=enhanced.macd,
        adx=enhanced.adx,
        atr14=enhanced.atr14,
        atr_pct=enhanced.atr_pct,
        donchian=enhanced.donchian,
        cross=enhanced.cross,
        bollinger=enhanced.bollinger,
        key_levels=key_levels,
        ma_alignment=ma_align,
        trend_score=trend_score,
        multi_tf=enhanced.multi_tf,
        signal_agreement=calc_signal_agreement(enhanced),
        distribution_days_25=calc_distribution_days(closes, volumes),
        liquidity=calc_liquidity(volumes),
        session=session,
        market_regime=regime.regime,
        market_regime_reason=regime.reason,
        session_limit=limit_state,
    )
