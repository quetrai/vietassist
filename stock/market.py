from __future__ import annotations

import asyncio
import logging
import time
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from stock.validation import ohlcv_contract_errors, validate_ohlcv

logger = logging.getLogger(__name__)

DNSE_BASE = "https://services.entrade.com.vn/chart-api/v2/ohlcs"
DNSE_TICK_API = "https://api.dnse.com.vn/price-api/query"
PRICE_SCALE = 1000
REALTIME_CACHE_TTL_SEC = 5
MIN_SESSIONS = 30
CACHE_MAX_ENTRIES = 500
DNSE_RETRY_ATTEMPTS = 2
DNSE_RETRY_BACKOFF_SEC = 0.6
VNSTOCK_TIMEOUT_SEC = 20

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
MARKET_CLOSE_HOUR = 15

_client: httpx.AsyncClient | None = None
_cache: OrderedDict[tuple[str, int], tuple[float, Series]] = OrderedDict()
_cache_locks: dict[tuple[str, int], asyncio.Lock] = {}
_locks_guard = asyncio.Lock()
_realtime_cache: dict[str, tuple[float, float]] = {}
_realtime_locks: dict[str, asyncio.Lock] = {}


@dataclass(frozen=True)
class Series:
    symbol: str
    closes: list[float]
    highs: list[float]
    lows: list[float]
    volumes: list[float]
    dates: list[str]
    source: str = "dnse"

    @property
    def price(self) -> float:
        return self.closes[-1] if self.closes else 0.0


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    prev_close: float
    change: float
    change_pct: float
    date: str
    is_realtime: bool


def _is_index_symbol(symbol: str) -> bool:
    return symbol.upper().replace("-", "").replace("^", "") in {
        "VNINDEX", "VN30", "HNXINDEX", "HNX30", "UPCOMINDEX", "UPINDEX"
    }


def _market_hours_now() -> bool:
    now = datetime.now(VN_TZ)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 <= minutes <= 15 * 60


async def _realtime_lock(symbol: str) -> asyncio.Lock:
    async with _locks_guard:
        lock = _realtime_locks.get(symbol)
        if lock is None:
            lock = asyncio.Lock()
            _realtime_locks[symbol] = lock
        return lock


async def fetch_realtime_tick(symbol: str, ttl: int = REALTIME_CACHE_TTL_SEC) -> float | None:
    """Lấy giá khớp gần nhất từ DNSE price-api; chỉ chấp nhận tick của hôm nay."""
    symbol = symbol.upper().strip()
    if _is_index_symbol(symbol) or not re.fullmatch(r"[A-Z0-9]{1,10}", symbol):
        return None
    cached = _realtime_cache.get(symbol)
    if cached and time.monotonic() - cached[0] < ttl:
        return cached[1]
    lock = await _realtime_lock(symbol)
    async with lock:
        cached = _realtime_cache.get(symbol)
        if cached and time.monotonic() - cached[0] < ttl:
            return cached[1]
        day_str = datetime.now(VN_TZ).strftime("%Y-%m-%d")
        query = (
            "query GetKrxTicksBySymbols {GetKrxTicksBySymbols("
            f'symbols: "{symbol}", date: "{day_str}", limit: 1, board: 2'
            ") {ticks {matchPrice}}}"
        )
        try:
            response = await client().post(
                DNSE_TICK_API,
                json={"operationName": "GetKrxTicksBySymbols", "query": query},
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
            ticks = ((data.get("data") or {}).get("GetKrxTicksBySymbols") or {}).get("ticks", [])
            if ticks:
                raw = ticks[0].get("matchPrice")
                if raw is not None and float(raw) > 0:
                    price = round(float(raw) * PRICE_SCALE)
                    _realtime_cache[symbol] = (time.monotonic(), price)
                    return price
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as exc:
            logger.warning("DNSE realtime tick lỗi cho %s: %s", symbol, exc)
        return None


async def fetch_quote(symbol: str, ttl: int = REALTIME_CACHE_TTL_SEC) -> Quote:
    """Quote ưu tiên tick realtime; trong giờ giao dịch không fallback về giá cũ."""
    symbol = symbol.upper().strip()
    series = await fetch(symbol, days=60, ttl=90)
    if len(series.closes) < 1:
        raise RuntimeError(f"Không có dữ liệu giá cho {symbol}")
    tick = await fetch_realtime_tick(symbol, ttl=ttl)
    today = datetime.now(VN_TZ).date().isoformat()
    if tick is not None:
        prev_close = (
            series.closes[-2]
            if len(series.closes) >= 2 and series.dates[-1] == today
            else series.closes[-1]
        )
        price = tick
        change = price - prev_close
        change_pct = (change / prev_close) * 100 if prev_close else 0
        return Quote(symbol, price, prev_close, change, change_pct, today, True)
    if _market_hours_now():
        raise RuntimeError(f"Nguồn realtime DNSE tạm thời không trả tick cho {symbol}; không dùng giá đóng cửa cũ thay thế")
    prev_close = series.closes[-2] if len(series.closes) >= 2 else series.closes[-1]
    price = series.closes[-1]
    change = price - prev_close
    change_pct = (change / prev_close) * 100 if prev_close else 0
    return Quote(symbol, price, prev_close, change, change_pct, series.dates[-1], False)

def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=10,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )
    return _client


async def close() -> None:
    global _client
    if _client:
        await _client.aclose()
        _client = None


async def _get_lock(key: tuple[str, int]) -> asyncio.Lock:
    async with _locks_guard:
        lock = _cache_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _cache_locks[key] = lock
        return lock


def _cache_get(key: tuple[str, int], ttl: int) -> Series | None:
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < ttl:
        return cached[1]
    return None


def _cache_put(key: tuple[str, int], series: Series) -> None:
    _cache[key] = (time.monotonic(), series)
    _cache.move_to_end(key)
    if len(_cache) > CACHE_MAX_ENTRIES:
        _cache.popitem(last=False)


async def fetch(symbol: str, days: int = 120, ttl: int = 90) -> Series:
    symbol = symbol.upper().strip()
    key = (symbol, days)
    cached = _cache_get(key, ttl)
    if cached is not None:
        return cached
    lock = await _get_lock(key)
    async with lock:
        cached = _cache_get(key, ttl)
        if cached is not None:
            return cached
        series = await _fetch_with_fallback(symbol, days)
        _cache_put(key, series)
        return series


async def _fetch_with_fallback(symbol: str, days: int) -> Series:
    """DNSE là nguồn chính (nhanh, không cần thư viện nặng). Nếu DNSE lỗi mạng, trả dữ
    liệu rỗng/không đủ phiên, hoặc vi phạm contract OHLCV (giá âm, high<low...), thử
    tiếp vnstock (nguồn VCI) trước khi báo lỗi hẳn cho người dùng — hai nguồn độc lập
    nên hiếm khi cùng lỗi một lúc."""
    try:
        series = await _fetch_from_dnse(symbol, days)
        validate(series)
        return series
    except Exception as exc:
        logger.warning("DNSE không dùng được cho %s (%s) — thử vnstock/VCI", symbol, exc)
    try:
        series = await _fetch_from_vnstock(symbol, days)
        validate(series)
        return series
    except Exception as exc:
        raise RuntimeError(f"Không lấy được dữ liệu {symbol} từ cả DNSE lẫn vnstock") from exc


async def _fetch_from_dnse(symbol: str, days: int) -> Series:
    endpoint = f"{DNSE_BASE}/{'index' if symbol == 'VNINDEX' else 'stock'}"
    now = int(datetime.now(UTC).timestamp())
    request_days = max(days, MIN_SESSIONS)
    params = {
        "symbol": symbol,
        "resolution": "1D",
        "from": now - request_days * 2 * 86400,
        "to": now,
    }
    data = None
    last_exc: Exception | None = None
    for attempt in range(DNSE_RETRY_ATTEMPTS + 1):
        try:
            response = await client().get(endpoint, params=params)
            response.raise_for_status()
            data = response.json()
            break
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < DNSE_RETRY_ATTEMPTS:
                await asyncio.sleep(DNSE_RETRY_BACKOFF_SEC * (attempt + 1))
    if data is None:
        raise RuntimeError(f"Không lấy được dữ liệu {symbol} từ DNSE") from last_exc
    arrays = [data.get(name) or [] for name in ("t", "c", "h", "l", "v")]
    size = min(map(len, arrays))
    if size < MIN_SESSIONS:
        raise ValueError(f"Không đủ dữ liệu cho {symbol}")
    timestamps, closes, highs, lows, volumes = (items[-size:][-days:] for items in arrays)
    scale = 1 if symbol == "VNINDEX" else 1000
    return Series(
        symbol,
        [round(float(value) * scale) for value in closes],
        [round(float(value) * scale) for value in highs],
        [round(float(value) * scale) for value in lows],
        [float(value or 0) for value in volumes],
        [datetime.fromtimestamp(int(value), UTC).date().isoformat() for value in timestamps],
        source="dnse",
    )


def _fetch_from_vnstock_sync(symbol: str, days: int) -> Series:
    from vnstock import Vnstock

    end = datetime.now(VN_TZ).date()
    start = end - timedelta(days=days * 2 + 30)
    df = (
        Vnstock()
        .stock(symbol=symbol, source="VCI")
        .quote.history(start=start.isoformat(), end=end.isoformat(), interval="1D")
    )
    if df is None or df.empty:
        raise RuntimeError(f"vnstock không có dữ liệu cho {symbol}")
    df = df.tail(days)
    cols = {str(column).strip().lower(): column for column in df.columns}

    def col(*names: str) -> str:
        for name in names:
            if name in cols:
                return cols[name]
        raise KeyError(f"Thiếu cột {names} trong dữ liệu vnstock cho {symbol}")

    date_col = col("time", "date", "trading_date")
    close_col = col("close")
    high_col = col("high")
    low_col = col("low")
    volume_col = col("volume", "match_volume")
    scale = 1 if symbol == "VNINDEX" else 1000
    closes, highs, lows, volumes, dates = [], [], [], [], []
    for _, row in df.iterrows():
        closes.append(round(float(row[close_col]) * scale))
        highs.append(round(float(row[high_col]) * scale))
        lows.append(round(float(row[low_col]) * scale))
        volumes.append(float(row[volume_col] or 0))
        dates.append(str(row[date_col])[:10])
    return Series(symbol, closes, highs, lows, volumes, dates, source="vnstock")


async def _fetch_from_vnstock(symbol: str, days: int) -> Series:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_fetch_from_vnstock_sync, symbol, days), timeout=VNSTOCK_TIMEOUT_SEC
        )
    except TimeoutError as exc:
        raise RuntimeError(f"vnstock timeout cho {symbol}") from exc
    except ImportError as exc:
        raise RuntimeError(
            "Thư viện vnstock chưa được cài — thêm 'vnstock' vào requirements.txt để dùng "
            "làm nguồn dự phòng khi DNSE lỗi"
        ) from exc


def validate(series: Series) -> None:
    errors = ohlcv_contract_errors(
        series.closes, series.highs, series.lows, series.volumes, series.dates
    )
    if errors:
        raise ValueError("; ".join(errors))
    quality = validate_ohlcv(series.closes, series.highs, series.lows, series.volumes, series.dates)
    if not quality.usable:
        raise ValueError("Dữ liệu OHLCV không đủ tin cậy: " + "; ".join(quality.reasons))


def trim_open_session(series: Series) -> Series:
    """Bỏ bar cuối nếu đó là phiên HÔM NAY và thị trường chưa đóng cửa (trước 15:00 giờ
    VN). Nến hôm nay lúc chưa đóng cửa là nến ĐANG CHẠY — giá "close" thực chất là giá
    khớp gần nhất tại thời điểm gọi API, không phải giá đóng cửa thật. Dùng nến này để
    tính SMA/RSI/support-resistance sẽ khiến hành động (BUY/WATCH/SELL...) đổi qua lại
    nhiều lần trong cùng một phiên tuỳ thời điểm người dùng hỏi. Chỉ dùng hàm này trước
    khi tính Features cho /stock — KHÔNG dùng cho /quote (nơi cần giá mới nhất kể cả
    đang khớp lệnh giữa phiên)."""
    if not series.dates:
        return series
    now_vn = datetime.now(VN_TZ)
    if series.dates[-1] != now_vn.date().isoformat() or now_vn.hour >= MARKET_CLOSE_HOUR:
        return series
    if len(series.closes) <= 1:
        return series
    return Series(
        series.symbol,
        series.closes[:-1],
        series.highs[:-1],
        series.lows[:-1],
        series.volumes[:-1],
        series.dates[:-1],
        series.source,
    )


async def fetch_pair(symbol: str, ttl: int = 90) -> tuple[Series, Series]:
    return await asyncio.gather(fetch(symbol, ttl=ttl), fetch("VNINDEX", ttl=ttl))
