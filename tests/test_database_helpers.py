from core import database


def test_rowcount_parses_delete_result():
    assert database._rowcount("DELETE 42") == 42


def test_rowcount_zero_rows():
    assert database._rowcount("DELETE 0") == 0


def test_rowcount_unexpected_format_returns_zero():
    assert database._rowcount("bogus") == 0
