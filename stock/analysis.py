from __future__ import annotations

import json
import re

from ai import router
from ai.contracts import TaskType
from core.config import settings
from stock.features import calculate
from stock.market import fetch, fetch_pair, trim_open_session
from stock.policy import Decision, evaluate

_SYMBOL = re.compile(r"^[A-Z]{3}$")


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if not _SYMBOL.fullmatch(symbol):
        raise ValueError("Mã cổ phiếu phải gồm 3 chữ cái")
    return symbol


async def quick_quote(symbol: str) -> str:
    symbol = normalize_symbol(symbol)
    series = await fetch(symbol, days=5, ttl=settings.stock_cache_ttl_sec)
    previous = series.closes[-2]
    change = (series.price / previous - 1) * 100
    return f"{symbol}: {series.price:,.0f}đ ({change:+.2f}%) — phiên {series.dates[-1]}".replace(
        ",", "."
    )


def _payload(symbol: str, decision: Decision, features: object, date: str) -> dict[str, object]:
    return {"symbol": symbol, "date": date, "features": vars(features), "decision": vars(decision)}


async def analyze_symbol(symbol: str, *, holding: bool = False, deep: bool = False) -> str:
    symbol = normalize_symbol(symbol)
    series, market = await fetch_pair(symbol, ttl=settings.stock_cache_ttl_sec)
    # Bỏ nến đang chạy (hôm nay, chưa đóng cửa) trước khi tính chỉ báo, để SMA/RSI/
    # support-resistance không đổi qua lại nhiều lần trong cùng 1 phiên tuỳ giờ hỏi.
    series = trim_open_session(series)
    market = trim_open_session(market)
    features = calculate(series, market)
    decision = evaluate(features, holding=holding)
    payload = _payload(symbol, decision, features, series.dates[-1])
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
    return response.text
