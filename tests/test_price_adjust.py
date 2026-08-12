from stock import price_adjust


def test_detect_price_gaps_ignores_normal_moves():
    closes = [100, 103, 98, 101]
    assert price_adjust.detect_price_gaps(closes) == []


def test_detect_price_gaps_flags_certain_gap_and_ratio_hint():
    # -50% ~ chia tỷ lệ 1:1 (thưởng cổ phiếu 1:1) -> vượt hẳn CERTAIN_GAP_PCT (25%).
    closes = [100, 50]
    dates = ["2026-08-05", "2026-08-06"]
    gaps = price_adjust.detect_price_gaps(closes, dates)

    assert len(gaps) == 1
    assert gaps[0].level == "certain"
    assert gaps[0].ratio_hint == "thưởng/chia tỷ lệ 1:1"
    assert gaps[0].date == "2026-08-06"


def test_detect_price_gaps_flags_suspect_but_not_certain():
    closes = [100, 85]  # -15%: trên suspect (12%) nhưng dưới certain (25%)
    gaps = price_adjust.detect_price_gaps(closes)

    assert len(gaps) == 1
    assert gaps[0].level == "suspect"


def test_infer_is_adjusted_returns_false_for_certain_gap():
    closes = [100, 50]
    assert price_adjust.infer_is_adjusted(closes) is False


def test_infer_is_adjusted_returns_none_when_no_certain_gap():
    closes = [100, 103, 98]
    assert price_adjust.infer_is_adjusted(closes) is None


def test_build_note_empty_when_no_gaps():
    assert price_adjust.build_note("FPT", "dnse", []) == ""


def test_build_note_certain_gap_warns_strongly():
    gaps = [price_adjust.PriceGap("2026-08-06", 100, 50, -50.0, "certain", "thưởng/chia tỷ lệ 1:1")]
    note = price_adjust.build_note("FPT", "dnse", gaps)

    assert "CHƯA ĐIỀU CHỈNH" in note
    assert "FPT" in note
    assert "2026-08-06" in note


def test_audit_series_end_to_end():
    closes = [100, 50, 51, 52]
    dates = ["2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"]
    audit = price_adjust.audit_series("FPT", "dnse", closes, dates)

    assert audit.is_adjusted is False
    assert len(audit.gaps) == 1
    assert audit.note != ""
