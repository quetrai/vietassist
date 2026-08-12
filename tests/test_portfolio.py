import services.portfolio as portfolio
from stock.market import Series


def _series(symbol: str, price: float) -> Series:
    return Series(symbol, [price], [price], [price], [1000.0], ["2026-08-07"])


async def test_buy_rejects_invalid_symbol():
    result = await portfolio.buy("u1", "FPTX", "100", "25000")
    assert "3 chữ cái" in result


async def test_buy_rejects_non_numeric_quantity():
    result = await portfolio.buy("u1", "FPT", "abc", "25000")
    assert "Cú pháp" in result


async def test_buy_rejects_zero_or_negative():
    result = await portfolio.buy("u1", "FPT", "0", "25000")
    assert "Cú pháp" in result


async def test_buy_success(monkeypatch):
    async def fake_upsert(user_id, symbol, quantity, price):
        assert (user_id, symbol, quantity, price) == ("u1", "FPT", 100.0, 25000.0)
        return 100.0, 25000.0

    monkeypatch.setattr(portfolio.database, "upsert_holding", fake_upsert)
    result = await portfolio.buy("u1", "fpt", "100", "25,000")
    assert "Đã ghi nhận mua FPT" in result
    assert "100 CP" in result


async def test_sell_reports_insufficient_quantity(monkeypatch):
    async def fake_reduce(user_id, symbol, quantity):
        return None

    monkeypatch.setattr(portfolio.database, "reduce_holding", fake_reduce)
    result = await portfolio.sell("u1", "FPT", "9999")
    assert "Không đủ" in result


async def test_sell_closes_position_at_zero(monkeypatch):
    async def fake_reduce(user_id, symbol, quantity):
        return 0.0

    monkeypatch.setattr(portfolio.database, "reduce_holding", fake_reduce)
    result = await portfolio.sell("u1", "FPT", "100")
    assert "đóng vị thế" in result


async def test_remove_not_found(monkeypatch):
    async def fake_delete(user_id, symbol):
        return False

    monkeypatch.setattr(portfolio.database, "delete_holding", fake_delete)
    result = await portfolio.remove("u1", "FPT")
    assert "Không có" in result


async def test_list_portfolio_empty(monkeypatch):
    async def fake_list_holdings(user_id):
        return []

    monkeypatch.setattr(portfolio.database, "list_holdings", fake_list_holdings)
    result = await portfolio.list_portfolio("u1")
    assert "Danh mục trống" in result


async def test_list_portfolio_computes_pnl(monkeypatch):
    async def fake_list_holdings(user_id):
        return [{"symbol": "FPT", "quantity": 100.0, "average_price": 20000.0}]

    async def fake_current_price(symbol, **kwargs):
        return 25000.0, False

    monkeypatch.setattr(portfolio.database, "list_holdings", fake_list_holdings)
    monkeypatch.setattr(portfolio, "_market_current_price", fake_current_price)

    result = await portfolio.list_portfolio("u1")
    assert "FPT" in result
    assert "+25.00%" in result
    assert "Tổng vốn" in result


async def test_list_portfolio_handles_price_fetch_failure(monkeypatch):
    async def fake_list_holdings(user_id):
        return [{"symbol": "FPT", "quantity": 100.0, "average_price": 20000.0}]

    async def fake_current_price(symbol, **kwargs):
        raise ValueError("Không đủ dữ liệu")

    monkeypatch.setattr(portfolio.database, "list_holdings", fake_list_holdings)
    monkeypatch.setattr(portfolio, "_market_current_price", fake_current_price)

    result = await portfolio.list_portfolio("u1")
    assert "không lấy được giá hiện tại" in result


async def test_list_portfolio_pnl_excludes_unpriced_holdings(monkeypatch):
    async def fake_list_holdings(user_id):
        return [
            {"symbol": "FPT", "quantity": 100.0, "average_price": 20000.0},
            {"symbol": "HPG", "quantity": 200.0, "average_price": 30000.0},
        ]

    async def fake_current_price(symbol, **kwargs):
        if symbol == "HPG":
            raise ValueError("Không đủ dữ liệu")
        return 25000.0, False

    monkeypatch.setattr(portfolio.database, "list_holdings", fake_list_holdings)
    monkeypatch.setattr(portfolio, "_market_current_price", fake_current_price)

    result = await portfolio.list_portfolio("u1")

    # PNL phải tính trên cặp vốn/giá trị của riêng FPT (mã có giá), không được lẫn
    # vốn của HPG (mã fetch lỗi) vào mẫu số rồi so với tử số chỉ có FPT.
    assert "+25.00%" in result
    assert "không tính 1 mã chưa lấy được giá" in result


async def test_buy_accepts_vietnamese_thousand_separator(monkeypatch):
    async def fake_upsert(user_id, symbol, quantity, price):
        assert (quantity, price) == (100, 26500)
        return quantity, price

    monkeypatch.setattr(portfolio.database, "upsert_holding", fake_upsert)
    result = await portfolio.buy("u1", "FPT", "100", "26.500")
    assert "Đã ghi nhận mua FPT" in result
