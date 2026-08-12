import services.portfolio as portfolio


async def test_set_alerts_rejects_invalid_symbol():
    result = await portfolio.set_alerts("u1", "FPTX", "20000", "30000")
    assert "3 chữ cái" in result


async def test_set_alerts_rejects_non_numeric_value():
    result = await portfolio.set_alerts("u1", "FPT", "abc", "30000")
    assert "Cú pháp" in result


async def test_set_alerts_reports_not_holding(monkeypatch):
    async def fake_set_holding_alerts(user_id, symbol, stop, target):
        return False

    monkeypatch.setattr(portfolio.database, "set_holding_alerts", fake_set_holding_alerts)
    result = await portfolio.set_alerts("u1", "FPT", "20000", "30000")
    assert "Không có FPT" in result


async def test_set_alerts_success(monkeypatch):
    captured = {}

    async def fake_set_holding_alerts(user_id, symbol, stop, target):
        captured["args"] = (user_id, symbol, stop, target)
        return True

    monkeypatch.setattr(portfolio.database, "set_holding_alerts", fake_set_holding_alerts)
    result = await portfolio.set_alerts("u1", "fpt", "20,000", "30,000")

    assert captured["args"][1] == "FPT"
    assert float(captured["args"][2]) == 20000
    assert float(captured["args"][3]) == 30000
    assert "stop 20.000đ" in result
    assert "target 30.000đ" in result


async def test_set_alerts_clears_one_side_with_dash(monkeypatch):
    captured = {}

    async def fake_set_holding_alerts(user_id, symbol, stop, target):
        captured["stop"] = stop
        captured["target"] = target
        return True

    monkeypatch.setattr(portfolio.database, "set_holding_alerts", fake_set_holding_alerts)
    result = await portfolio.set_alerts("u1", "FPT", "-", "30000")

    assert captured["stop"] is None
    assert float(captured["target"]) == 30000
    assert "stop chưa đặt" in result


async def test_list_portfolio_shows_stop_target_markers(monkeypatch):
    async def fake_list_holdings(user_id):
        return [
            {
                "symbol": "FPT", "quantity": 100.0, "average_price": 20000.0,
                "stop_price": 30000.0, "target_price": 22000.0,
            }
        ]

    async def fake_current_price(symbol, **kwargs):
        return 25000.0, False  # dưới target (22000 đã bị vượt) và trên stop giả lập cao hơn giá

    monkeypatch.setattr(portfolio.database, "list_holdings", fake_list_holdings)
    monkeypatch.setattr(portfolio, "_market_current_price", fake_current_price)

    result = await portfolio.list_portfolio("u1")

    assert "stop 30.000đ ⚠️ đã chạm/dưới stop" in result
    assert "target 22.000đ 🎯 đã chạm/vượt target" in result


async def test_list_portfolio_hides_alert_section_when_not_set(monkeypatch):
    async def fake_list_holdings(user_id):
        return [{"symbol": "FPT", "quantity": 100.0, "average_price": 20000.0}]

    async def fake_current_price(symbol, **kwargs):
        return 25000.0, False

    monkeypatch.setattr(portfolio.database, "list_holdings", fake_list_holdings)
    monkeypatch.setattr(portfolio, "_market_current_price", fake_current_price)

    result = await portfolio.list_portfolio("u1")

    assert "stop" not in result
    assert "target" not in result
