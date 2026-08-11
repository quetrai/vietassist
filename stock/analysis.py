from __future__ import annotations

import json
import re

from ai import router
from ai.contracts import TaskType
from core.config import settings
from stock.features import calculate
from stock.fundamentals import build_fundamentals_prompt_section, fetch_fundamentals
from stock.market import fetch, fetch_pair, fetch_quote, trim_open_session
from stock.policy import Decision, evaluate
from stock.price_adjust import audit_series
from stock.report_format import clean_analysis_output
from stock.sector import get_primary_sector_label

_SYMBOL = re.compile(r"^[A-Z]{3}$")


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if not _SYMBOL.fullmatch(symbol):
        raise ValueError("Mã cổ phiếu phải gồm 3 chữ cái")
    return symbol


async def quick_quote(symbol: str) -> str:
    symbol = normalize_symbol(symbol)
    quote = await fetch_quote(symbol)
    label = "khớp lệnh REALTIME" if quote.is_realtime else "giá đóng cửa gần nhất (ngoài giờ giao dịch)"
    return (
        f"{symbol}: {quote.price:,.0f}đ ({quote.change:+,.0f}đ, {quote.change_pct:+.2f}%) — {label}, {quote.date}"
    ).replace(",", ".")


def _payload(symbol: str, decision: Decision, features: object, date: str) -> dict[str, object]:
    return {"symbol": symbol, "date": date, "features": vars(features), "decision": vars(decision)}


async def analyze_symbol(symbol: str, *, holding: bool = False, deep: bool = False) -> str:
    symbol = normalize_symbol(symbol)
    series, market = await fetch_pair(symbol, ttl=settings.stock_cache_ttl_sec)
    
    
    series = trim_open_session(series)
    market = trim_open_session(market)
    features = calculate(series, market)
    decision = evaluate(features, holding=holding)
    audit = audit_series(symbol, series.source, series.closes, series.dates)
    payload = _payload(symbol, decision, features, series.dates[-1])
    payload["sector"] = get_primary_sector_label(symbol)
    payload["data_quality"] = {"source": series.source, "price_adjusted": audit.is_adjusted, "warning": audit.note}
    if deep:
        fundamentals = await fetch_fundamentals(symbol)
        fundamental_section = build_fundamentals_prompt_section(
            fundamentals.valuation,
            fundamentals.foreign,
            symbol,
            fundamentals.foreign_trend,
            fundamentals.growth,
            fundamentals.events,
            fundamentals.sector_pe_avg,
            fundamentals.sector_pe_sample,
            fundamentals.sector_pe_label,
            fundamentals.sector_profile,
            fundamentals.sector_benchmark,
        )
        if fundamental_section:
            payload["fundamentals"] = fundamental_section
    system = """Bạn diễn giải báo cáo cổ phiếu Việt Nam từ JSON deterministic.
Không đổi action, confidence, giá, stop, target hoặc R:R. Không thêm số liệu ngoài JSON.
Nêu ngày dữ liệu, lý do, rủi ro và kịch bản. Đây là thông tin tham khảo, không phải tư vấn đầu tư."""
    content = json.dumps(payload, ensure_ascii=False)
    if deep:
        system += (
            "\nĐây là báo cáo sâu: phân tích kỹ hơn các kịch bản tăng/giảm, so sánh với vùng giá "
            "gần đây, và nêu rõ điều kiện khiến nhận định đảo chiều."
        )
        response = await router.deep_report(
            [{"role": "user", "content": content}], system=system, temperature=0.4
        )
    else:
        response = await router.text(
            TaskType.STOCK_NARRATIVE,
            [{"role": "user", "content": content}],
            system=system,
            temperature=0.2,
        )
    return clean_analysis_output(response.text)
