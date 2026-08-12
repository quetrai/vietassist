"""Chuẩn hoá fundamentals theo ngành — port từ repo Gemini
(`stock/fundamental_profiles.py`).

Vì sao cần: P/E vô nghĩa với ngân hàng/chứng khoán/bảo hiểm (lợi nhuận biến
động mạnh theo chu kỳ trích lập dự phòng/tự doanh), nên nhóm này nên nhìn P/B
là chính. D/E và current ratio kiểu doanh nghiệp sản xuất cũng không áp dụng
được cho ngân hàng (cấu trúc bảng cân đối hoàn toàn khác) nên bị suppress.
"""
from __future__ import annotations

from dataclasses import dataclass

from stock import sector


@dataclass(frozen=True)
class FundamentalProfile:
    key: str
    label: str
    benchmark_metric: str
    priority_metrics: tuple[str, ...]
    suppress_metrics: tuple[str, ...] = ()
    note: str = ""


PROFILES: dict[str, FundamentalProfile] = {
    "banking": FundamentalProfile(
        "banking", "Ngân hàng", "pb", ("pb", "roe", "eps", "profit_growth"),
        ("current_ratio", "debt_equity"),
        "Ưu tiên P/B và ROE; D/E/current ratio kiểu doanh nghiệp sản xuất không phù hợp.",
    ),
    "securities": FundamentalProfile(
        "securities", "Chứng khoán", "pb", ("pb", "roe", "profit_growth"),
        ("current_ratio",),
        "Ưu tiên P/B, ROE và độ nhạy lợi nhuận theo thanh khoản thị trường.",
    ),
    "insurance": FundamentalProfile(
        "insurance", "Bảo hiểm", "pb", ("pb", "roe", "profit_growth"),
        ("current_ratio",),
        "Ưu tiên P/B và ROE; cần đối chiếu dự phòng khi có dữ liệu.",
    ),
    "realestate": FundamentalProfile(
        "realestate", "Bất động sản", "pb", ("pb", "debt_equity", "current_ratio", "profit_growth"),
        (),
        "Ưu tiên tài sản ròng, đòn bẩy, thanh khoản và tiến độ dự án.",
    ),
    "utilities": FundamentalProfile(
        "utilities", "Điện & Tiện ích", "pe", ("pe", "dividend_yield", "debt_equity", "roe"),
        (),
        "Ưu tiên dòng tiền/cổ tức và đòn bẩy hạ tầng.",
    ),
    "oilgas": FundamentalProfile(
        "oilgas", "Dầu khí", "pe", ("pe", "roe", "profit_growth", "debt_equity"),
        (),
        "Đọc định giá cùng chu kỳ giá hàng hoá.",
    ),
    "default": FundamentalProfile(
        "default", "Doanh nghiệp", "pe", ("pe", "pb", "roe", "profit_growth", "debt_equity", "current_ratio"),
    ),
}


def get_profile(symbol: str) -> FundamentalProfile:
    for key in sector.get_symbol_sectors(symbol):
        if key in PROFILES:
            return PROFILES[key]
    return PROFILES["default"]
