from __future__ import annotations

import asyncio
import re
from decimal import Decimal, InvalidOperation

from core import database
from stock.analysis import normalize_symbol
from stock.market import current_price as _market_current_price

_GROUPED = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")
_DECIMAL = re.compile(r"^\d+[.,]\d+$")


def _parse_positive_number(raw: str) -> Decimal | None:
    raw = raw.strip()
    if _GROUPED.fullmatch(raw):
        normalized = raw.replace(".", "").replace(",", "")
    elif _DECIMAL.fullmatch(raw):
        sep = max(raw.rfind(","), raw.rfind("."))
        normalized = f"{raw[:sep]}.{raw[sep + 1 :]}"
    elif raw.isdigit():
        normalized = raw
    else:
        return None
    try:
        value = Decimal(normalized)
    except InvalidOperation:
        return None
    return value if value > 0 else None


def _as_decimal(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _vn(value: Decimal) -> str:
    """Định dạng số nguyên kiểu Việt Nam (dấu chấm phân cách hàng nghìn). Không dùng
    str.replace trên toàn bộ câu vì nó sẽ làm hỏng dấu phẩy ngăn cách mệnh đề trong văn
    bản tiếng Việt xung quanh — chỉ áp dụng cho từng số riêng lẻ."""
    return f"{value:,.0f}".replace(",", ".")


async def buy(user_id: str, symbol_raw: str, quantity_raw: str, price_raw: str) -> str:
    try:
        symbol = normalize_symbol(symbol_raw)
    except ValueError as exc:
        return str(exc)
    quantity = _parse_positive_number(quantity_raw)
    price = _parse_positive_number(price_raw)
    if quantity is None or price is None:
        return "Cú pháp: /muavao <MÃ> <khối lượng> <giá mua (đ)>, cả hai phải lớn hơn 0."
    total_qty, avg_price = await database.upsert_holding(user_id, symbol, quantity, price)
    return (
        f"Đã ghi nhận mua {symbol}. Tổng: {_vn(total_qty)} CP, giá vốn bình quân {_vn(avg_price)}đ."
    )


async def sell(user_id: str, symbol_raw: str, quantity_raw: str) -> str:
    try:
        symbol = normalize_symbol(symbol_raw)
    except ValueError as exc:
        return str(exc)
    quantity = _parse_positive_number(quantity_raw)
    if quantity is None:
        return "Cú pháp: /banra <MÃ> <khối lượng lớn hơn 0>"
    remaining = await database.reduce_holding(user_id, symbol, quantity)
    if remaining is None:
        return f"Không đủ {symbol} trong danh mục để bán {_vn(quantity)} CP."
    if remaining == 0:
        return f"Đã bán hết {symbol}, đóng vị thế."
    return f"Đã bán {_vn(quantity)} {symbol}. Còn lại {_vn(remaining)} CP."


async def remove(user_id: str, symbol_raw: str) -> str:
    try:
        symbol = normalize_symbol(symbol_raw)
    except ValueError as exc:
        return str(exc)
    found = await database.delete_holding(user_id, symbol)
    return f"Đã xóa {symbol} khỏi danh mục." if found else f"Không có {symbol} trong danh mục."


async def set_alerts(user_id: str, symbol_raw: str, stop_raw: str, target_raw: str) -> str:
    """Đặt/xoá mức giá stop-loss/chốt lời THAM KHẢO cho 1 mã đang giữ. Dùng "-" cho
    stop hoặc target để xoá riêng mức đó (giữ nguyên mức còn lại).

    ⚠️ CHỈ lưu để hiển thị lại trong /danhmuc — KHÔNG có cơ chế tự động kiểm tra giá
    theo chu kỳ và bắn cảnh báo khi chạm mức (cần thêm 1 vòng lặp nền kiểu
    reminder_loop, xem services/reminders.py, chưa được xây — nếu cần alert chủ động,
    dùng /nhac để tự đặt nhắc nhở kiểm tra giá theo giờ mong muốn)."""
    try:
        symbol = normalize_symbol(symbol_raw)
    except ValueError as exc:
        return str(exc)
    stop = None if stop_raw.strip() == "-" else _parse_positive_number(stop_raw)
    target = None if target_raw.strip() == "-" else _parse_positive_number(target_raw)
    if (stop is None and stop_raw.strip() != "-") or (target is None and target_raw.strip() != "-"):
        return "Cú pháp: /muctieu <MÃ> <giá stop hoặc -> <giá target hoặc ->, giá phải lớn hơn 0."
    found = await database.set_holding_alerts(user_id, symbol, stop, target)
    if not found:
        return f"Không có {symbol} trong danh mục. Dùng /muavao trước."
    stop_text = f"{_vn(stop)}đ" if stop else "chưa đặt"
    target_text = f"{_vn(target)}đ" if target else "chưa đặt"
    return f"Đã cập nhật {symbol}: stop {stop_text}, target {target_text}."


async def list_portfolio(user_id: str) -> str:
    rows = await database.list_holdings(user_id)
    if not rows:
        return "Danh mục trống. Dùng /muavao <MÃ> <khối lượng> <giá> để thêm."

    holdings = [
        (
            str(row["symbol"]),
            _as_decimal(row["quantity"]),
            _as_decimal(row["average_price"]),
            _as_decimal(row["stop_price"]) if row.get("stop_price") is not None else None,
            _as_decimal(row["target_price"]) if row.get("target_price") is not None else None,
        )
        for row in rows
    ]
    prices = await asyncio.gather(*(_current_price(symbol) for symbol, *_ in holdings))

    lines: list[str] = []
    priced_cost = Decimal(0)
    priced_value = Decimal(0)
    unpriced = 0
    for (symbol, qty, avg, stop, target), price in zip(holdings, prices, strict=True):
        lines.append(_format_holding_line(symbol, qty, avg, price, stop, target))
        if price is None:
            unpriced += 1
            continue
        priced_cost += qty * avg
        priced_value += qty * price

    summary = ""
    if priced_cost:
        pnl_pct = (priced_value / priced_cost - 1) * 100
        note = f", không tính {unpriced} mã chưa lấy được giá" if unpriced else ""
        summary = (
            f"\n\nTổng vốn {_vn(priced_cost)}đ, giá trị hiện tại {_vn(priced_value)}đ "
            f"({pnl_pct:+.2f}%){note}"
        )
    return "\n".join(lines) + summary


async def _current_price(symbol: str) -> Decimal | None:
    try:
        price, _is_realtime = await _market_current_price(symbol)
    except (ValueError, RuntimeError):
        return None
    return _as_decimal(price) if price else None


def _format_holding_line(
    symbol: str,
    qty: Decimal,
    avg: Decimal,
    price: Decimal | None = None,
    stop: Decimal | None = None,
    target: Decimal | None = None,
) -> str:
    if price is None:
        return f"{symbol}: {_vn(qty)} CP @ vốn {_vn(avg)}đ — không lấy được giá hiện tại"
    pnl_pct = (price / avg - 1) * 100
    line = f"{symbol}: {_vn(qty)} CP @ vốn {_vn(avg)}đ, giá hiện tại {_vn(price)}đ ({pnl_pct:+.2f}%)"
    if stop is None and target is None:
        return line
    parts = []
    if stop is not None:
        marker = " ⚠️ đã chạm/dưới stop" if price <= stop else ""
        parts.append(f"stop {_vn(stop)}đ{marker}")
    if target is not None:
        marker = " 🎯 đã chạm/vượt target" if price >= target else ""
        parts.append(f"target {_vn(target)}đ{marker}")
    return f"{line} — " + ", ".join(parts)
