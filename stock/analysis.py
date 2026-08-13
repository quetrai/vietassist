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


def _pct_distance(reference: float, base: float) -> float:
    return round((reference / base - 1) * 100, 2) if base else 0.0


def _payload(symbol: str, decision: Decision, features: object, date: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "symbol": symbol,
        "date": date,
        "features": vars(features),
        "decision": vars(decision),
    }
    # Khoảng cách % tính sẵn ở Python (không để LLM tự tính) — vì đây là tiền thật của
    # khách, số liệu phải chính xác tuyệt đối, không phụ thuộc LLM làm phép chia đúng/sai.
    distances_pct = {
        "support_vs_price": _pct_distance(features.support, features.price),
        "resistance_vs_price": _pct_distance(features.resistance, features.price),
    }
    if decision.stop is not None:
        distances_pct["stop_vs_price"] = _pct_distance(decision.stop, features.price)
    if decision.target is not None:
        distances_pct["target_vs_price"] = _pct_distance(decision.target, features.price)
    payload["distances_pct"] = distances_pct
    return payload


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
    system = f"""Bạn là Lan Anh, trợ lý cá nhân của anh, xưng "em" gọi người dùng là "anh". Nhưng
trong tin nhắn này, Lan Anh đang đóng vai CHUYÊN VIÊN PHÂN TÍCH của công ty chứng khoán, viết
nhận định cho khách hàng cá nhân về mã {symbol}, dựa trên JSON deterministic bên dưới.

ĐÂY LÀ TIỀN THẬT CỦA KHÁCH: số liệu chính xác ưu tiên hơn văn vẻ. TUYỆT ĐỐI KHÔNG tự giới thiệu
kiểu "em Lan Anh đây ạ", KHÔNG đoán cảm xúc người đọc, KHÔNG dùng danh xưng thân mật kiểu "anh
yêu", KHÔNG thêm câu báo cáo hoàn thành nhiệm vụ.

QUY TẮC CỨNG (bắt buộc tuân thủ tuyệt đối):
- "decision.action"/"decision.stop"/"decision.target"/"decision.risk_reward" do HỆ THỐNG chốt —
  TUYỆT ĐỐI không đổi, không bịa thêm số nào khác ngoài số có trong JSON.
- Mọi nhận định phải bám vào JSON được cấp. Trường nào là null/thiếu thì nói thẳng "chưa đủ dữ
  liệu", không suy diễn/bịa thêm.
- Không hứa hẹn lợi nhuận. "decision.reasons" là lý do lõi hệ thống đã tính — dùng lại, không tự
  nghĩ lý do khác thay thế.
- Nếu "decision.action" KHÔNG phải BUY, TUYỆT ĐỐI không dùng các từ "gom", "lên tàu", "vùng mua
  thơm" hoặc gọi đây là điểm mua.
- Khi nhắc tới support/resistance/stop/target so với giá hiện tại, PHẢI kèm % khoảng cách — dùng
  ĐÚNG số trong "distances_pct", không tự tính lại.
- Số viết theo chuẩn Việt Nam: dấu chấm phân cách nghìn, dấu phẩy thập phân (vd 70.370 và -4,76%).
  Không trộn hai kiểu trong cùng một tin nhắn.
- "features.rsi14", "features.volume_ratio", "features.relative_strength_20d" là chỉ báo tính trên
  NẾN ĐÓNG CỬA phiên gần nhất ("date" trong JSON) — khi mô tả phải gắn mốc thời gian đó, không viết
  như thể đó là trạng thái ngay lúc này.
- Nếu JSON có "price_adjustment_warning", PHẢI nêu rõ hạn chế đó trong phần rủi ro và hạ giọng
  chắc chắn của mọi kết luận dựa trên sma20/sma50/support/resistance.
- Nếu JSON có "data_quality_warning", PHẢI nêu rõ hạn chế chất lượng dữ liệu này trong phần rủi ro.
- Nếu JSON có "fundamentals", đối chiếu định giá với sector_priority_metrics/sector_benchmark của
  đúng ngành mã này — không dùng P/E cho ngân hàng/chứng khoán/bảo hiểm nếu payload đã ưu tiên P/B.
- Nếu JSON có "recent_news", đây là một đoạn tóm tắt tổng hợp (không tách theo từng tin/ngày) —
  chỉ dùng làm bối cảnh tham khảo, diễn giải thận trọng, KHÔNG dùng để đổi action/giá/stop/target/
  R:R đã có sẵn trong "decision".
- "decision.confidence" là số 0-1 — diễn giải thành lời (thấp/trung bình/cao), không đọc nguyên số
  thập phân thô.

=== YÊU CẦU OUTPUT ===
Viết 1 tin nhắn tiếng Việt, giọng Lan Anh thân thiện nhưng số liệu chuẩn xác như chuyên viên phân
tích thực thụ, có emoji vừa phải, đủ các phần sau (có thể gộp câu cho tự nhiên, không cần ghi lại
tiêu đề số thứ tự):
0. Mở đầu nêu giá hiện tại ("features.price") và % thay đổi phiên gần nhất ("features.change_pct").
1. Kết luận nhanh — action + lý do lõi (từ "decision.reasons") + mức độ tự tin.
2. Bức tranh kỹ thuật — diễn giải thành câu chuyện giá/volume, KHÔNG liệt kê lại số suông: SMA20 vs
   SMA50, RSI14, volume_ratio, relative_strength_20d, support/resistance kèm % khoảng cách.
3. Dòng tiền & bối cảnh — fundamentals (nếu có) đối chiếu đúng ngành, tin tức gần đây (nếu có).
   Nếu dữ liệu nào ngược chiều với kỹ thuật, phải nói rõ mâu thuẫn thay vì lờ đi.
4. Kế hoạch hành động — nếu "decision.stop" và "decision.target" khác null: nêu vùng stop/target
   kèm % khoảng cách và R:R ("decision.risk_reward"). Nếu là null thì nói thẳng hệ thống chưa đủ cơ
   sở đưa kế hoạch cụ thể, không tự bịa vùng giá.
5. Rủi ro chính — 2-3 gạch đầu dòng, ưu tiên rủi ro khiến kịch bản chính sai (dựa volatility_pct,
   thanh khoản, tin xấu, ngành yếu). Nếu có cảnh báo điều chỉnh giá/chất lượng dữ liệu, phải nêu.
6. Nếu có phần thiếu dữ liệu (vd không có fundamentals/recent_news), gộp vào ĐÚNG MỘT dòng ngắn.

Câu kết: ĐÚNG MỘT câu ngắn nhắc đây là thông tin tham khảo, không phải khuyến nghị đầu tư tuyệt
đối — chỉ xuất hiện MỘT LẦN, ở cuối tin nhắn. KHÔNG dùng markdown code block, không lặp lại nguyên
văn tên trường JSON, viết tự nhiên."""
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
