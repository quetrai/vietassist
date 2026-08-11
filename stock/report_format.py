from __future__ import annotations

import re

_SELF_INTRO_RE = re.compile(r"^\s*(anh|chị)\s*ơi[,!\s]*em\s+lan\s+anh\s+đây\s*(ạ)?\s*[!.,]*\s*", re.I)
_TASK_DONE_RE = re.compile(r"[^\n]*(nhiệm\s+vụ|công\s+việc)[^\n]*(xong|hoàn\s+thành)[^\n]*\n?", re.I)


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


def clean_analysis_output(text: str) -> str:
    text = _SELF_INTRO_RE.sub("", text or "")
    text = _TASK_DONE_RE.sub("", text)
    return text.strip()
