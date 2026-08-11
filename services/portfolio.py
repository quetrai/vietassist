from __future__ import annotations

import asyncio
import re
from decimal import Decimal, InvalidOperation

from core import database
from stock.analysis import normalize_symbol
from stock.market import fetch_quote

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


async def list_portfolio(user_id: str) -> str:
    rows = await database.list_holdings(user_id)
    if not rows:
        return "Danh mục trống. Dùng /muavao <MÃ> <khối lượng> <giá> để thêm."

    holdings = [
        (str(row["symbol"]), _as_decimal(row["quantity"]), _as_decimal(row["average_price"]))
        for row in rows
    ]
    prices = await asyncio.gather(*(_current_price(symbol) for symbol, _, _ in holdings))

    lines: list[str] = []
    priced_cost = Decimal(0)
    priced_value = Decimal(0)
    unpriced = 0
    for (symbol, qty, avg), price in zip(holdings, prices, strict=True):
        lines.append(_format_holding_line(symbol, qty, avg, price))
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
        quote = await fetch_quote(symbol)
    except (ValueError, RuntimeError):
        return None
    return _as_decimal(quote.price)


def _format_holding_line(
    symbol: str, qty: Decimal, avg: Decimal, price: Decimal | None = None
) -> str:
    if price is None:
        return f"{symbol}: {_vn(qty)} CP @ vốn {_vn(avg)}đ — không lấy được giá hiện tại"
    pnl_pct = (price / avg - 1) * 100
    return (
        f"{symbol}: {_vn(qty)} CP @ vốn {_vn(avg)}đ, giá hiện tại {_vn(price)}đ ({pnl_pct:+.2f}%)"
    )
