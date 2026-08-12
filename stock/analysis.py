from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime

from ai import router
from ai.contracts import TaskType
from core.config import settings
from stock import fundamentals, news, price_adjust, validation
from stock.features import calculate
from stock.market import VN_TZ, fetch, fetch_pair, fetch_realtime_tick, trim_open_session
from stock.policy import Decision, evaluate

logger = logging.getLogger(__name__)
_SYMBOL = re.compile(r"^[A-Z]{3}$")


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if not _SYMBOL.fullmatch(symbol):
        raise ValueError("Mã cổ phiếu phải gồm 3 chữ cái")
    return symbol


async def quick_quote(symbol: str) -> str:
    symbol = normalize_symbol(symbol)
    series = await fetch(symbol, days=5, ttl=settings.stock_cache_ttl_sec)
    if len(series.closes) < 2:
        raise RuntimeError(f"Không đủ dữ liệu cho {symbol}")
    realtime_price = await fetch_realtime_tick(symbol)
    today = datetime.now(VN_TZ).strftime("%Y-%m-%d")
    if realtime_price is not None:
        # Tick realtime hôm nay: so sánh với giá đóng cửa phiên GẦN NHẤT TRƯỚC ĐÓ,
        # không phải closes[-2] mù quáng — nếu OHLC chưa có nến hôm nay, closes[-1]
        # mới là phiên trước.
        previous = series.closes[-2] if series.dates[-1] == today else series.closes[-1]
        price, label, tag = realtime_price, today, " · realtime"
    else:
        price, previous, label, tag = series.price, series.closes[-2], series.dates[-1], ""
    change = (price / previous - 1) * 100 if previous else 0.0
    return f"{symbol}: {price:,.0f}đ ({change:+.2f}%) — phiên {label}{tag}".replace(",", ".")


def _payload(symbol: str, decision: Decision, features: object, date: str) -> dict[str, object]:
    return {"symbol": symbol, "date": date, "features": vars(features), "decision": vars(decision)}


async def _safe_fundamentals_payload(symbol: str) -> dict[str, object] | None:
    try:
        bundle = await fundamentals.fetch_fundamentals(symbol)
        return fundamentals.to_payload(bundle, symbol)
    except Exception:
        logger.warning("fundamentals payload lỗi cho %s", symbol, exc_info=True)
        return None


async def _safe_symbol_news(symbol: str) -> str | None:
    try:
        return await news.fetch_symbol_news(symbol)
    except Exception:
        logger.warning("symbol news lỗi cho %s", symbol, exc_info=True)
        return None


async def analyze_symbol(symbol: str, *, holding: bool = False, deep: bool = False) -> str:
    symbol = normalize_symbol(symbol)
    (series, market), fundamentals_payload, recent_news = await asyncio.gather(
        fetch_pair(symbol, ttl=settings.stock_cache_ttl_sec),
        _safe_fundamentals_payload(symbol),
        _safe_symbol_news(symbol),
    )
    # Cổng chất lượng dữ liệu MỀM (xem stock/validation.py) — chạy TRƯỚC khi cắt nến
    # hôm nay, trên đúng chuỗi vừa fetch. "bad" (quá ít phiên / vi phạm contract) thì
    # chặn hẳn, không phân tích tiếp; "degraded" (dữ liệu cũ / biến động bất thường /
    # hơi ít phiên) vẫn phân tích nhưng phải nêu rõ hạn chế trong payload.
    quality = validation.validate_ohlcv(series.closes, series.highs, series.lows, series.volumes, series.dates)
    if not quality.usable:
        raise RuntimeError("dữ liệu không đủ tin cậy để phân tích: " + "; ".join(quality.reasons))
    # Kiểm tra chuỗi giá đã điều chỉnh sau chia tách/cổ tức chưa TRƯỚC khi cắt
    # nến hôm nay — cần nhìn full lịch sử vừa fetch để phát hiện gap đúng.
    audit = price_adjust.audit_series(symbol, series.source, series.closes, series.dates)
    # Bỏ nến đang chạy (hôm nay, chưa đóng cửa) trước khi tính chỉ báo, để SMA/RSI/
    # support-resistance không đổi qua lại nhiều lần trong cùng 1 phiên tuỳ giờ hỏi.
    series = trim_open_session(series)
    market = trim_open_session(market)
    features = calculate(series, market)
    decision = evaluate(features, holding=holding)
    payload = _payload(symbol, decision, features, series.dates[-1])
    if fundamentals_payload:
        payload["fundamentals"] = fundamentals_payload
    if audit.note:
        payload["price_adjustment_warning"] = audit.note
    if quality.reasons:
        payload["data_quality_warning"] = quality.reasons
    if recent_news:
        payload["recent_news"] = recent_news
    system = """Bạn diễn giải báo cáo cổ phiếu Việt Nam từ JSON deterministic.
Không đổi action, confidence, giá, stop, target hoặc R:R. Không thêm số liệu ngoài JSON.
Nêu ngày dữ liệu, lý do, rủi ro và kịch bản. Đây là thông tin tham khảo, không phải tư vấn đầu tư.
Nếu JSON có "fundamentals", đối chiếu định giá với sector_priority_metrics/sector_benchmark của
đúng ngành mã này — không dùng P/E cho ngân hàng/chứng khoán/bảo hiểm nếu payload đã ưu tiên P/B.
Nếu JSON có "price_adjustment_warning", PHẢI nêu rõ hạn chế này trong phần rủi ro.
Nếu JSON có "data_quality_warning", PHẢI nêu rõ hạn chế chất lượng dữ liệu này trong phần rủi ro.
Nếu JSON có "recent_news", dùng làm bối cảnh tham khảo khi diễn giải kịch bản — KHÔNG được
dùng nó để đổi action/giá/stop/target/R:R đã có sẵn trong "decision"."""
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
