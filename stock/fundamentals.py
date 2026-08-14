"""Định giá cơ bản (P/E, P/B, EPS, ROE, D/E, current ratio) chuẩn hoá theo
ngành, port RÚT GỌN từ repo Gemini (`stock/fundamentals.py`). Dùng thư viện
`vnstock` (đã là dependency có sẵn — dùng làm nguồn dự phòng OHLCV trong
`stock/market.py`) để lấy chỉ số tài chính công khai từ VCI. Không cần API key.

⚠️ QUAN TRỌNG:
- `vnstock` là thư viện bên thứ 3 dựa trên API công khai không tài liệu hoá
  chính thức của VCI → KHÔNG có SLA, có thể lỗi hoặc đổi cấu trúc dữ liệu bất
  kỳ lúc nào mà không báo trước.
- Toàn bộ hàm ở đây match tên cột theo TỪ KHOÁ (substring) thay vì tên cột
  cứng, để bớt nhạy cảm với thay đổi nhỏ giữa các phiên bản vnstock — nhưng
  KHÔNG đảm bảo luôn đúng 100%. Không tìm thấy cột phù hợp → trả None cho
  trường đó thay vì đoán liều.
- Gọi vnstock là thao tác ĐỒNG BỘ (blocking) → luôn chạy qua
  `asyncio.to_thread()` với timeout, không bao giờ raise ra ngoài
  `fetch_fundamentals()` — mất fundamentals không được làm hỏng cả `/stock`.
- Khối ngoại (`fetch_foreign_flow`) và lịch sự kiện (`fetch_upcoming_events`) lấy qua
  API nội bộ TCBS (khác VCI ở trên) — CŨNG không chính thức/không tài liệu hoá, cùng rủi
  ro như trên. Tách hàm riêng, timeout riêng, không bao giờ raise ra ngoài — thiếu 1
  trong 2 không được làm hỏng phần định giá đã lấy được từ VCI.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from stock import fundamental_profiles, sector

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT_SEC = 15
_PE_HISTORY_QUARTERS = 20  # ~5 năm dữ liệu quý, dùng tính percentile P/E
_SECTOR_SAMPLE_SIZE = 8


@dataclass
class Valuation:
    pe: float | None = None
    pb: float | None = None
    eps: float | None = None
    roe: float | None = None
    dividend_yield: float | None = None
    debt_equity: float | None = None
    current_ratio: float | None = None
    pe_percentile: float | None = None  # 0-100: P/E hiện tại cao/thấp hơn bao nhiêu % lịch sử
    pe_history_quarters: int = 0


@dataclass
class SectorBenchmark:
    metric: str  # "pe" hoặc "pb" — theo FundamentalProfile.benchmark_metric
    average: float | None
    sample: int
    label: str | None


@dataclass
class FundamentalsBundle:
    valuation: Valuation | None = None
    sector_profile: fundamental_profiles.FundamentalProfile | None = None
    sector_benchmark: SectorBenchmark | None = None
    foreign_flow: "ForeignFlow | None" = None
    upcoming_events: "list[UpcomingEvent]" = field(default_factory=list)


def _to_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        result = float(value)  # type: ignore[arg-type]
        if result != result:  # NaN
            return None
        return result
    except (TypeError, ValueError):
        return None


def _flatten_columns(columns) -> list[str]:
    flat = []
    for col in columns:
        if isinstance(col, tuple):
            flat.append("_".join(str(c) for c in col if c).strip().lower())
        else:
            flat.append(str(col).strip().lower())
    return flat


def _find_col(flat_columns: list[str], *keywords: str) -> int | None:
    """Trả về index cột đầu tiên chứa TẤT CẢ keyword (không phân biệt hoa/thường)."""
    for i, col in enumerate(flat_columns):
        if all(kw in col for kw in keywords):
            return i
    return None


def _find_col_any(flat_columns: list[str], *keyword_groups: tuple[str, ...]) -> int | None:
    """Thử lần lượt từng nhóm keyword — vnstock đặt tên cột tiếng Việt hoặc
    tiếng Anh tuỳ version/lang."""
    for group in keyword_groups:
        idx = _find_col(flat_columns, *group)
        if idx is not None:
            return idx
    return None


_RATIO_COL_DENYLIST = ("period", "type", "length")


def _find_ratio_col(flat_columns: list[str], primary: str, fallback: str) -> int | None:
    """Ưu tiên match tên cột đầy đủ (vd "p/e"); fallback substring ngắn (vd
    "pe") chỉ được chấp nhận khi tên cột không chứa từ trong denylist — tránh
    khớp nhầm "period"/"period_length"/"type" chứa "pe" như substring tình cờ."""
    idx = _find_col(flat_columns, primary)
    if idx is not None:
        return idx
    for i, col in enumerate(flat_columns):
        if fallback in col and not any(bad in col for bad in _RATIO_COL_DENYLIST):
            return i
    return None


def _percentile_rank(current: float, history: list[float]) -> float:
    if not history:
        return 50.0
    below = sum(1 for value in history if value < current)
    return round(below / len(history) * 100, 1)


def _fetch_valuation_sync(symbol: str) -> Valuation | None:
    try:
        from vnstock import Vnstock
    except ImportError:
        logger.warning("Chưa cài thư viện vnstock (pip install vnstock).")
        return None

    try:
        stock = Vnstock().stock(symbol=symbol, source="VCI")
    except Exception:
        logger.warning("vnstock: không khởi tạo được cho %s", symbol, exc_info=True)
        return None

    df = None
    for kwargs in ({"period": "quarter"}, {}):
        try:
            df = stock.finance.ratio(**kwargs)
            if df is not None and not df.empty:
                break
        except Exception:
            continue
    if df is None or df.empty:
        return None

    flat_cols = _flatten_columns(df.columns)

    # Đảm bảo quý gần nhất luôn ở iloc[0]: không giả định df đã sắp xếp sẵn.
    year_idx = _find_col(flat_cols, "year")
    quarter_idx = _find_col_any(flat_cols, ("quarter",), ("length",))
    if year_idx is not None:
        sort_cols = [df.columns[year_idx]]
        if quarter_idx is not None:
            sort_cols.append(df.columns[quarter_idx])
        df = df.sort_values(by=sort_cols, ascending=False).reset_index(drop=True)

    row = df.iloc[0]

    def _val(*keywords: str) -> float | None:
        idx = _find_col(flat_cols, *keywords)
        return _to_float(row.iloc[idx]) if idx is not None else None

    pe_idx = _find_ratio_col(flat_cols, "p/e", "pe")
    pe = _to_float(row.iloc[pe_idx]) if pe_idx is not None else None
    pb_idx = _find_ratio_col(flat_cols, "p/b", "pb")
    pb = _to_float(row.iloc[pb_idx]) if pb_idx is not None else None
    eps = _val("eps")
    roe = _val("roe")
    dividend_yield_a = _val("dividend", "yield")
    dividend_yield_b = _val("dividend", "suất")
    dividend_yield = dividend_yield_a if dividend_yield_a is not None else dividend_yield_b
    if dividend_yield is None:
        dividend_yield = _val("dividend")
    if dividend_yield is not None and dividend_yield > 40:
        # Không tỷ suất cổ tức thật nào ở VN vượt mức này → nhiều khả năng cột
        # lấy được là dividend per share (VND), không phải %.
        dividend_yield = None
    debt_equity_idx = _find_col_any(
        flat_cols, ("nợ", "vốn chủ"), ("debt", "equity"), ("nợ/vcsh",)
    )
    debt_equity = _to_float(row.iloc[debt_equity_idx]) if debt_equity_idx is not None else None
    current_ratio_idx = _find_col_any(flat_cols, ("thanh toán", "hiện"), ("current", "ratio"))
    current_ratio = (
        _to_float(row.iloc[current_ratio_idx]) if current_ratio_idx is not None else None
    )

    pe_percentile = None
    pe_quarters = 0
    if pe_idx is not None and pe is not None:
        history = []
        for value in df.iloc[:_PE_HISTORY_QUARTERS, pe_idx]:
            parsed = _to_float(value)
            if parsed is not None and parsed > 0:
                history.append(parsed)
        pe_quarters = len(history)
        if pe_quarters >= 4:  # dưới 1 năm dữ liệu thì percentile không có nhiều ý nghĩa
            pe_percentile = _percentile_rank(pe, history)

    return Valuation(
        pe=pe, pb=pb, eps=eps, roe=roe, dividend_yield=dividend_yield,
        debt_equity=debt_equity, current_ratio=current_ratio,
        pe_percentile=pe_percentile, pe_history_quarters=pe_quarters,
    )


async def fetch_valuation(symbol: str) -> Valuation | None:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_fetch_valuation_sync, symbol), timeout=_FETCH_TIMEOUT_SEC
        )
    except Exception:
        logger.warning("fetch_valuation lỗi cho %s", symbol, exc_info=True)
        return None


async def fetch_sector_benchmark(
    symbol: str, sample_size: int = _SECTOR_SAMPLE_SIZE
) -> SectorBenchmark:
    """So P/E hoặc P/B (tuỳ ngành) hiện tại với trung bình MỘT MẪU NHỎ mã cùng
    ngành — KHÔNG phải trung bình toàn ngành chính xác qua screener (sẽ cần
    rất nhiều request, chậm và dễ bị giới hạn), chỉ là ước lượng nhanh từ vài
    mã tiêu biểu. `sample` thấp thì độ tin cậy của trung bình cũng thấp."""
    profile = fundamental_profiles.get_profile(symbol)
    keys = sector.get_symbol_sectors(symbol)
    if not keys:
        return SectorBenchmark(profile.benchmark_metric, None, 0, None)
    meta = sector.SECTOR_MAP[keys[0]]
    peers = [p for p in meta["symbols"] if p != symbol.upper()][:sample_size]
    if not peers:
        return SectorBenchmark(profile.benchmark_metric, None, 0, meta["label"])

    async def _load(peer: str) -> float | None:
        try:
            valuation = await asyncio.wait_for(
                asyncio.to_thread(_fetch_valuation_sync, peer), timeout=_FETCH_TIMEOUT_SEC
            )
            value = getattr(valuation, profile.benchmark_metric, None) if valuation else None
            return value if value is not None and 0 < value < 500 else None
        except Exception:
            return None

    values = [v for v in await asyncio.gather(*(_load(p) for p in peers)) if v is not None]
    average = round(sum(values) / len(values), 2) if values else None
    return SectorBenchmark(profile.benchmark_metric, average, len(values), meta["label"])


@dataclass
class ForeignFlow:
    ownership_pct: float | None = None       # % cổ phần do NĐT nước ngoài nắm giữ hiện tại
    net_volume_20s_pct: float | None = None  # % KLGD ròng khối ngoại so với tổng KLGD, gộp ~20 phiên gần nhất
    exchange: str | None = None              # HOSE/HNX/UPCOM - dùng để tính biên độ trần/sàn (stock/features.py)


def _fetch_foreign_flow_sync(symbol: str) -> ForeignFlow | None:
    """Khối ngoại + sàn niêm yết lấy qua API nội bộ TCBS (khác VCI dùng cho valuation ở
    trên) — ownership_pct/exchange từ Company.overview() (cột 'foreignPercent'/'exchange',
    tên rõ ràng, độ tin cậy cao). net_volume_20s_pct từ Trading.price_board() (cột 'nstp'
    = '%KLGD ròng(CM)') — tên cột KHÔNG ghi rõ "khối ngoại" trong chính thư viện vnstock,
    suy luận từ ngữ cảnh UI TCBS nên độ tin cậy thấp hơn 2 trường kia; sanity-check biên
    độ [-100, 100] trước khi trả về, bỏ qua nếu vượt (nhiều khả năng đọc nhầm cột)."""
    try:
        from vnstock import Trading
        from vnstock.explorer.tcbs.company import Company as TcbsCompany
    except ImportError:
        return None

    ownership_pct = None
    exchange = None
    try:
        overview = TcbsCompany(symbol).overview()
        if overview is not None and not overview.empty:
            if "foreign_percent" in overview.columns:
                ownership_pct = _to_float(overview.iloc[0]["foreign_percent"])
                if ownership_pct is not None:
                    ownership_pct = round(ownership_pct * 100, 2) if ownership_pct <= 1 else round(ownership_pct, 2)
            if "exchange" in overview.columns:
                raw_exchange = str(overview.iloc[0]["exchange"] or "").strip().upper()
                exchange = raw_exchange or None
    except Exception:
        logger.warning("vnstock: không lấy được overview (foreign_percent/exchange) cho %s", symbol, exc_info=True)

    net_pct = None
    try:
        board = Trading(symbol=symbol, show_log=False).price_board([symbol])
        if board is not None and not board.empty and "%KLGD ròng (CM)" in board.columns:
            value = _to_float(board.iloc[0]["%KLGD ròng (CM)"])
            if value is not None and -100 <= value <= 100:
                net_pct = round(value, 2)
    except Exception:
        logger.warning("vnstock: không lấy được KLGD ròng khối ngoại cho %s", symbol, exc_info=True)

    if ownership_pct is None and net_pct is None and exchange is None:
        return None
    return ForeignFlow(ownership_pct=ownership_pct, net_volume_20s_pct=net_pct, exchange=exchange)


async def fetch_foreign_flow(symbol: str) -> ForeignFlow | None:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_fetch_foreign_flow_sync, symbol), timeout=_FETCH_TIMEOUT_SEC
        )
    except Exception:
        logger.warning("fetch_foreign_flow lỗi cho %s", symbol, exc_info=True)
        return None


@dataclass
class UpcomingEvent:
    date: str | None
    title: str


_EVENTS_LOOKAHEAD = 5


def _fetch_upcoming_events_sync(symbol: str) -> list[UpcomingEvent]:
    try:
        from vnstock.explorer.tcbs.company import Company as TcbsCompany
    except ImportError:
        return []
    try:
        df = TcbsCompany(symbol).events(page_size=_EVENTS_LOOKAHEAD)
    except Exception:
        logger.warning("vnstock: không lấy được lịch sự kiện cho %s", symbol, exc_info=True)
        return []
    if df is None or df.empty:
        return []
    events: list[UpcomingEvent] = []
    date_col = next((c for c in df.columns if "date" in str(c).lower()), None)
    title_col = next((c for c in df.columns if "title" in str(c).lower() or "name" in str(c).lower()), None)
    if title_col is None:
        return []
    for _, row in df.head(_EVENTS_LOOKAHEAD).iterrows():
        title = str(row.get(title_col) or "").strip()
        if not title:
            continue
        date_value = str(row.get(date_col))[:10] if date_col else None
        events.append(UpcomingEvent(date=date_value, title=title))
    return events


async def fetch_upcoming_events(symbol: str) -> list[UpcomingEvent]:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_fetch_upcoming_events_sync, symbol), timeout=_FETCH_TIMEOUT_SEC
        )
    except Exception:
        logger.warning("fetch_upcoming_events lỗi cho %s", symbol, exc_info=True)
        return []


async def fetch_fundamentals(symbol: str) -> FundamentalsBundle:
    """Lấy song song định giá + benchmark ngành. Không bao giờ raise ra ngoài
    — thiếu fundamentals không được làm hỏng cả báo cáo `/stock`."""
    try:
        valuation, benchmark, foreign_flow, events = await asyncio.gather(
            fetch_valuation(symbol),
            fetch_sector_benchmark(symbol),
            fetch_foreign_flow(symbol),
            fetch_upcoming_events(symbol),
        )
    except Exception:
        logger.warning("fetch_fundamentals lỗi cho %s", symbol, exc_info=True)
        return FundamentalsBundle(sector_profile=fundamental_profiles.get_profile(symbol))
    return FundamentalsBundle(
        valuation=valuation,
        sector_profile=fundamental_profiles.get_profile(symbol),
        sector_benchmark=benchmark,
        foreign_flow=foreign_flow,
        upcoming_events=events,
    )


def to_payload(bundle: FundamentalsBundle, symbol: str) -> dict[str, object] | None:
    """Chuyển bundle sang dict gọn để đưa vào JSON payload deterministic cho
    `/stock` — khớp kiến trúc vietassist (LLM chỉ diễn giải JSON có sẵn, không
    tự tính số). Trả None nếu không có gì để nói (tránh prompt rỗng gây LLM
    tự bịa)."""
    valuation = bundle.valuation
    benchmark = bundle.sector_benchmark
    if valuation is None and (benchmark is None or benchmark.average is None):
        return None
    profile = bundle.sector_profile or fundamental_profiles.get_profile(symbol)
    payload: dict[str, object] = {
        "sector": profile.label,
        "sector_priority_metrics": list(profile.priority_metrics),
    }
    if profile.note:
        payload["sector_note"] = profile.note
    if valuation:
        metrics: dict[str, object] = {
            "pe": valuation.pe,
            "pb": valuation.pb,
            "eps": valuation.eps,
            "roe_pct": valuation.roe,
            "dividend_yield_pct": valuation.dividend_yield,
        }
        if not ({"debt_equity", "current_ratio"} <= set(profile.suppress_metrics)):
            metrics["debt_equity"] = valuation.debt_equity
            metrics["current_ratio"] = valuation.current_ratio
        else:
            metrics["debt_equity_note"] = "không áp dụng cho ngành này, đã ẩn"
        payload["valuation"] = metrics
        if valuation.pe_percentile is not None:
            payload["pe_percentile_vs_own_history"] = {
                "percentile": valuation.pe_percentile,
                "quarters": valuation.pe_history_quarters,
                "note": "percentile càng cao = P/E đang càng đắt so với lịch sử CHÍNH mã này, không phải so ngành",
            }
    if benchmark and benchmark.average is not None:
        payload["sector_benchmark"] = {
            "metric": "P/B" if benchmark.metric == "pb" else "P/E",
            "average": benchmark.average,
            "sample_size": benchmark.sample,
            "sector_label": benchmark.label,
            "note": f"ước lượng nhanh từ {benchmark.sample} mã tiêu biểu cùng ngành, không phải toàn ngành",
        }
    if bundle.foreign_flow and bundle.foreign_flow.exchange:
        payload["exchange"] = bundle.foreign_flow.exchange
    if bundle.foreign_flow and (
        bundle.foreign_flow.ownership_pct is not None or bundle.foreign_flow.net_volume_20s_pct is not None
    ):
        foreign: dict[str, object] = {}
        if bundle.foreign_flow.ownership_pct is not None:
            foreign["ownership_pct"] = bundle.foreign_flow.ownership_pct
        if bundle.foreign_flow.net_volume_20s_pct is not None:
            foreign["net_volume_pct_of_total"] = bundle.foreign_flow.net_volume_20s_pct
            foreign["net_volume_note"] = (
                "dương = khối ngoại mua ròng, âm = bán ròng, tính trên KLGD gần đây - nguồn không "
                "chính thức, độ tin cậy trung bình, chỉ dùng tham khảo"
            )
        payload["foreign_flow"] = foreign
    if bundle.upcoming_events:
        payload["upcoming_events"] = [
            {"date": e.date, "title": e.title} for e in bundle.upcoming_events if e.title
        ]
    return payload
