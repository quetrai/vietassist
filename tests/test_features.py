import pytest

from stock.features import pct_change, rsi, sma


def test_sma():
    assert sma(list(range(1, 21)), 20) == 10.5


def test_pct_change():
    assert pct_change([100, 110], 1) == pytest.approx(10)


def test_rsi_uptrend():
    assert rsi(list(range(1, 17))) == 100
