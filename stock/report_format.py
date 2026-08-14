from __future__ import annotations

import re

_SELF_INTRO_RE = re.compile(r"^\s*(anh|chị)\s*ơi[,!\s]*em\s+lan\s+anh\s+đây\s*(ạ)?\s*[!.,]*\s*", re.I)
_TASK_DONE_RE = re.compile(r"[^\n]*(nhiệm\s+vụ|công\s+việc)[^\n]*(xong|hoàn\s+thành)[^\n]*\n?", re.I)

# Lop chan cuoi cho bao cao /stock - khong phu thuoc vao viec LLM co tuan lenh trong
# system prompt hay khong. Model nho/yeu (vd groq gpt-oss-20b, provider dau tien duoc
# thu cho STOCK_NARRATIVE) thinh thoang lam 2 kieu loi voi payload JSON dai:
#   1. Chen nguyen van khoi JSON/code fence vao giua cau tra loi tieng Viet.
#   2. Lac ngon ngu: chen tu tieng Trung/Phap/Y... xen ke tieng Viet (vd "sopra",
#      "assez", chu Han). Khong the fix tin cay bang regex cho tung tu don le (do la
#      tu that trong ngon ngu khac, khong phai loi chinh ta), nhung ky tu CJK/Cyrillic/
#      Hangul thi CHAC CHAN sai vi tieng Viet khong bao gio dung cac bang chu do - xoa
#      thang duoc, khong so xoa nham.

# Fenced code block (```...``` hoac ```json ... ```) - LLM doi khi boc JSON trong day
# du prompt da cam "khong dung markdown code block".
_CODE_FENCE_RE = re.compile(r"```[a-zA-Z]*\n?.*?```", re.DOTALL)

# JSON tho khong boc trong code fence: mot khoi bat dau bang { va co it nhat 1 cap
# "key": xuat hien trong do - dau hieu ro rang la du lieu payload bi echo nguyen van
# thay vi duoc dien giai thanh loi van. Dung khop dau ngoac can bang nong can (khong ho
# tro nested sau vo han qua regex thuan, nhung du dung cho cac payload {..."a": 1, "b":
# {...}} nong 1-2 cap thuc te cua module nay).
_BARE_JSON_BLOCK_RE = re.compile(
    r"\{(?:[^{}]|\{[^{}]*\})*\}"
)
_JSON_KEY_HINT_RE = re.compile(r'"[a-zA-Z_][a-zA-Z0-9_]*"\s*:')

# Cac bang chu KHONG BAO GIO xuat hien trong tieng Viet hop le - CJK (Trung/Nhat),
# Hangul (Han), Cyrillic (Nga), Ả Rập, Thái... Any ky tu thuoc cac dai nay giua mot bao
# cao tieng Viet chac chan la loi model, xoa an toan.
_FOREIGN_SCRIPT_RE = re.compile(
    "["
    "\u4e00-\u9fff"  # CJK Unified Ideographs (Han/Kanji/Hanja)
    "\u3040-\u30ff"  # Hiragana + Katakana
    "\uac00-\ud7af"  # Hangul syllables
    "\u0400-\u04ff"  # Cyrillic
    "\u0600-\u06ff"  # Arabic
    "\u0e00-\u0e7f"  # Thai
    "]+"
)


def fmt_price(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.0f}".replace(",", ".")


def fmt_number(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.{decimals}f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def fmt_pct(value: float | None, decimals: int = 2) -> str:
    return "N/A" if value is None else f"{fmt_number(value, decimals)}%"


def _strip_leaked_json(text: str) -> str:
    text = _CODE_FENCE_RE.sub("", text)

    def _drop_if_json_like(match: re.Match[str]) -> str:
        block = match.group(0)
        # Chi xoa neu trong ruot co dau hieu key JSON that su ("field": ...) - tranh
        # xoa nham mot cau tieng Viet vo tinh co dau { } (vd trich dan bieu thuc).
        return "" if _JSON_KEY_HINT_RE.search(block) else block

    return _BARE_JSON_BLOCK_RE.sub(_drop_if_json_like, text)


def _strip_foreign_scripts(text: str) -> str:
    return _FOREIGN_SCRIPT_RE.sub("", text)


def _collapse_whitespace(text: str) -> str:
    # Sau khi xoa cac khoi JSON/chu nuoc ngoai, don lai khoang trang/dong trong thua de
    # khong de lai "lo hong" giua cau.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def clean_analysis_output(text: str) -> str:
    """Lam sach dau ra LLM truoc khi gui cho nguoi dung trong bao cao /stock.

    Day la lop chan CUOI CUNG, chay bat ke system prompt co duoc tuan thu hay khong -
    xem ghi chu o dau file ve 2 loi hay gap: JSON bi echo nguyen van va lac ngon ngu.
    """
    if not text:
        return ""
    text = _SELF_INTRO_RE.sub("", text)
    text = _TASK_DONE_RE.sub("", text)
    text = _strip_leaked_json(text)
    text = _strip_foreign_scripts(text)
    text = _collapse_whitespace(text)
    return text.strip()
