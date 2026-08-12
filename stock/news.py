"""Tin tức gần đây theo mã cổ phiếu, dùng làm NGỮ CẢNH THAM KHẢO cho `/stock`.

Khác với repo Gemini (dùng danh sách `NewsHeadline` có tiêu đề + điểm sentiment
riêng, tự tính `news_impact` đưa thẳng vào `stock/policy.py` để ảnh hưởng
action mua/bán), ở đây CỐ Ý đơn giản hơn và KHÔNG đổi quyết định deterministic:
chỉ gọi lại `ai.router.macro_news()` (Google Search grounding — nguồn thật,
có ngày tháng, fail-closed nếu không xác minh được) giới hạn vào đúng mã, rồi
đưa nguyên văn tóm tắt vào payload JSON cho LLM diễn giải làm bối cảnh — action/
giá/stop/target vẫn chỉ do `stock/policy.py` quyết định, không đổi theo tin tức.

Lý do không tự tính sentiment/impact: vietassist không có nguồn tin có cấu
trúc (title + ngày + nguồn tách riêng) như Gemini — chỉ có 1 khối text đã được
Google Search grounding tổng hợp sẵn. Tự chấm điểm sentiment từ text đó thêm
1 lượt suy đoán không chắc chắn, trong khi mục tiêu ở đây chỉ là "cho AI diễn
giải thêm bối cảnh", không phải thay đổi con số.
"""
from __future__ import annotations

import logging
import time

from ai import router
from ai.contracts import GroundingUnavailable, ProviderError

logger = logging.getLogger(__name__)

_CACHE_TTL_SEC = 900  # 15 phút — đủ mới cho /stock, tránh gọi search lặp lại liên tục
_MAX_CHARS = 700  # cắt bớt trước khi nhét vào payload JSON, tránh phình prompt
_cache: dict[str, tuple[float, str | None]] = {}


async def fetch_symbol_news(symbol: str) -> str | None:
    """Trả về đoạn tóm tắt tin tức gần đây về `symbol`, hoặc None nếu không tra được
    (chưa cấu hình grounding, lỗi provider, hoặc không có tin xác minh được) — KHÔNG
    bao giờ raise ra ngoài, thiếu tin tức không được làm hỏng cả `/stock`."""
    symbol = symbol.strip().upper()
    cached = _cache.get(symbol)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL_SEC:
        return cached[1]
    try:
        response = await router.macro_news(f"tin tức mới nhất liên quan cổ phiếu {symbol} Việt Nam")
        text = response.text.strip()
    except (GroundingUnavailable, ProviderError) as exc:
        logger.info("fetch_symbol_news(%s) không có kết quả: %s", symbol, exc)
        text = None
    except Exception:
        logger.warning("fetch_symbol_news(%s) lỗi không mong đợi", symbol, exc_info=True)
        text = None
    if text:
        text = text[:_MAX_CHARS].strip()
    _cache[symbol] = (time.monotonic(), text or None)
    return text or None
