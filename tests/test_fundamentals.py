import stock.fundamentals as fundamentals
from stock.fundamental_profiles import get_profile


def test_get_profile_uses_pb_for_banking():
    profile = get_profile("VCB")
    assert profile.key == "banking"
    assert profile.benchmark_metric == "pb"
    assert "current_ratio" in profile.suppress_metrics


def test_get_profile_defaults_to_pe_for_unknown_symbol():
    profile = get_profile("ZZZ")
    assert profile.key == "default"
    assert profile.benchmark_metric == "pe"


def test_percentile_rank_low_when_current_is_cheapest():
    assert fundamentals._percentile_rank(5.0, [10.0, 12.0, 15.0, 20.0]) == 0.0


def test_percentile_rank_high_when_current_is_most_expensive():
    assert fundamentals._percentile_rank(25.0, [10.0, 12.0, 15.0, 20.0]) == 100.0


async def test_fetch_sector_benchmark_averages_peer_pb_for_banking(monkeypatch):
    async def fake_load(symbol, **kwargs):
        return fundamentals.Valuation(pb={"VCB": 2.0, "BID": 1.8, "CTG": 2.2}.get(symbol))

    monkeypatch.setattr(fundamentals, "fetch_valuation", fake_load)
    # fetch_sector_benchmark gọi _fetch_valuation_sync qua asyncio.to_thread, không
    # phải fetch_valuation — monkeypatch trực tiếp hàm sync để test không đụng mạng.
    monkeypatch.setattr(
        fundamentals,
        "_fetch_valuation_sync",
        lambda symbol: fundamentals.Valuation(pb={"VCB": 2.0, "BID": 1.8, "CTG": 2.2}.get(symbol)),
    )

    benchmark = await fundamentals.fetch_sector_benchmark("VCB", sample_size=3)

    assert benchmark.metric == "pb"
    assert benchmark.sample >= 1
    assert benchmark.label == "Ngân hàng"


async def test_fetch_sector_benchmark_returns_empty_for_symbol_without_sector(monkeypatch):
    benchmark = await fundamentals.fetch_sector_benchmark("ZZZ")
    assert benchmark.average is None
    assert benchmark.sample == 0


def test_to_payload_returns_none_when_nothing_available():
    bundle = fundamentals.FundamentalsBundle()
    assert fundamentals.to_payload(bundle, "ZZZ") is None


def test_to_payload_hides_debt_equity_for_banking():
    bundle = fundamentals.FundamentalsBundle(
        valuation=fundamentals.Valuation(pe=10, pb=1.5, roe=18, debt_equity=99, current_ratio=1.1),
        sector_profile=get_profile("VCB"),
        sector_benchmark=fundamentals.SectorBenchmark("pb", 1.9, 5, "Ngân hàng"),
    )
    payload = fundamentals.to_payload(bundle, "VCB")

    assert payload["sector"] == "Ngân hàng"
    assert "debt_equity" not in payload["valuation"]
    assert payload["valuation"]["pb"] == 1.5
    assert payload["sector_benchmark"]["metric"] == "P/B"
    assert payload["sector_benchmark"]["average"] == 1.9


def test_to_payload_keeps_debt_equity_for_default_profile():
    bundle = fundamentals.FundamentalsBundle(
        valuation=fundamentals.Valuation(pe=12, debt_equity=0.5, current_ratio=1.8),
        sector_profile=get_profile("FPT"),
    )
    payload = fundamentals.to_payload(bundle, "FPT")

    assert payload["valuation"]["debt_equity"] == 0.5
    assert payload["valuation"]["current_ratio"] == 1.8
