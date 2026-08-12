"""Bản đồ ngành cổ phiếu Việt Nam — dùng để chuẩn hoá fundamentals theo ngành
(P/B ưu tiên ngân hàng/chứng khoán/bảo hiểm/bất động sản, P/E các ngành còn
lại) và ước lượng benchmark từ vài mã tiêu biểu cùng ngành trong `/stock`.

Port RÚT GỌN từ repo Gemini (`stock/sector.py`): chỉ giữ phần bản đồ ngành +
tra cứu. Phần phân tích luân chuyển dòng tiền theo ngành (sector rotation,
"dòng tiền đang vào ngành nào") của bản gốc KHÔNG được port — đó là một tính
năng riêng, chưa được yêu cầu, và cần thêm hạ tầng cache/async riêng để không
làm `/stock` chậm đi.
"""
from __future__ import annotations

SECTOR_MAP: dict[str, dict] = {
    "banking": {
        "label": "Ngân hàng",
        "symbols": [
            "VCB", "BID", "CTG", "TCB", "MBB", "ACB", "VPB", "HDB", "STB", "EIB",
            "TPB", "SHB", "VIB", "LPB", "SSB", "MSB", "OCB", "NAB", "BAB", "ABB",
        ],
    },
    "steel": {"label": "Thép", "symbols": ["HPG", "HSG", "NKG", "TLH", "SMC", "VGS", "TVN"]},
    "realestate": {
        "label": "Bất động sản",
        "symbols": [
            "VIC", "VHM", "NVL", "KDH", "DXG", "PDR", "NLG", "DIG", "VRE", "KBC",
            "BCM", "HDC", "IJC", "SCR", "TCH", "AGG", "NTL", "QCG", "LDG", "HQC", "ITA", "TDC",
        ],
    },
    "oilgas": {
        "label": "Dầu khí",
        "symbols": ["GAS", "PLX", "PVD", "PVT", "PVS", "BSR", "OIL", "PLC", "PVC", "PVB", "PGD", "CNG"],
    },
    "technology": {"label": "Công nghệ", "symbols": ["FPT", "CMG", "VGI", "CTR", "ELC", "ITD", "SGT", "FOX"]},
    "securities": {
        "label": "Chứng khoán",
        "symbols": ["SSI", "VCI", "HCM", "VND", "VIX", "SHS", "MBS", "BVS", "FTS", "BSI", "CTS", "AGR", "VDS", "ORS", "DSC"],
    },
    "retail": {"label": "Bán lẻ", "symbols": ["MWG", "FRT", "PNJ", "DGW", "PET", "HAX"]},
    "food": {
        "label": "Thực phẩm & Đồ uống",
        "symbols": ["VNM", "SAB", "MSN", "DBC", "HAG", "QNS", "MCH", "KDC", "SBT", "BAF", "HNG", "LSS"],
    },
    "seafood": {"label": "Thuỷ sản", "symbols": ["VHC", "ANV", "FMC", "IDI", "MPC", "ASM", "ACL", "CMX"]},
    "rubber": {"label": "Cao su & Săm lốp", "symbols": ["GVR", "PHR", "DPR", "DRC", "CSM", "DRI", "TRC", "RTB"]},
    "chemicals": {"label": "Hoá chất & Phân bón", "symbols": ["DGC", "DPM", "DCM", "CSV", "LAS", "BFC", "DDV"]},
    "insurance": {"label": "Bảo hiểm", "symbols": ["BVH", "BMI", "PVI", "MIG", "PTI", "BIC", "ABI"]},
    "aviation": {"label": "Hàng không", "symbols": ["HVN", "VJC", "ACV", "SCS", "SAS", "AST", "NCT"]},
    "textile": {"label": "Dệt may", "symbols": ["VGT", "TNG", "MSH", "TCM", "STK", "GIL"]},
    "pharma": {"label": "Dược phẩm & Y tế", "symbols": ["DHG", "IMP", "DBD", "DVN", "DHT"]},
    "materials": {
        "label": "Vật liệu xây dựng",
        "symbols": ["VGC", "HT1", "BMP", "NTP", "VCS", "PTB", "BCC", "CVT", "KSB", "DHA"],
    },
    "industrial_park": {"label": "Khu công nghiệp", "symbols": ["KBC", "BCM", "IDC", "SIP"]},
    "construction": {"label": "Xây dựng & Hạ tầng", "symbols": ["CTD", "VCG", "HHV", "CII", "LCG", "FCN", "TCD"]},
    "electrical": {"label": "Thiết bị điện & Công nghiệp", "symbols": ["GEX", "REE", "PC1"]},
    "utilities": {
        "label": "Điện & Tiện ích",
        "symbols": ["POW", "REE", "GAS", "PLC", "NT2", "PC1", "GEG", "VSH", "QTP", "HDG"],
    },
    "logistics": {
        "label": "Vận tải & Logistics",
        "symbols": ["GMD", "PVT", "ACV", "VJC", "VTP", "HAH", "VSC", "VOS", "TMS", "PHP"],
    },
}

ALL_KNOWN_SYMBOLS: set[str] = {s for meta in SECTOR_MAP.values() for s in meta["symbols"]}


def get_symbol_sectors(symbol: str) -> list[str]:
    """Một mã có thể thuộc nhiều ngành (vd REE ở cả electrical lẫn utilities)."""
    normalized = symbol.strip().upper()
    return [key for key, meta in SECTOR_MAP.items() if normalized in meta["symbols"]]


def get_primary_sector_label(symbol: str) -> str:
    keys = get_symbol_sectors(symbol)
    return SECTOR_MAP[keys[0]]["label"] if keys else "Khác"
