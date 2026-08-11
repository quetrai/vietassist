from __future__ import annotations

import re
from dataclasses import dataclass

from core import database
from services import reminders


@dataclass(frozen=True)
class ToolResult:
    handled: bool
    text: str = ""


_NOTE_PATTERNS = (
    re.compile(r"^(?:ghi chú|ghi chu|note lại|note lai)\s+(.+)$", re.I),
)
_PORTFOLIO_PATTERNS = (
    "xem danh mục",
    "xem danh muc",
    "danh mục của tôi",
    "danh muc cua toi",
    "đang giữ mã nào",
    "dang giu ma nao",
)


def _extract_note(text: str) -> str | None:
    for pattern in _NOTE_PATTERNS:
        match = pattern.match(text.strip())
        if match:
            return match.group(1).strip()
    return None


async def maybe_run(user_id: str, text: str) -> ToolResult:
    normalized = " ".join(text.casefold().split())
    note = _extract_note(text)
    if note:
        note_id = await database.add_note(user_id, note)
        return ToolResult(True, f"Đã lưu ghi chú #{note_id}.")

    if any(pattern in normalized for pattern in _PORTFOLIO_PATTERNS):
        db = await database.pool()
        holdings = await db.fetch(
            "SELECT symbol, quantity, average_price FROM stock_holdings WHERE user_id = $1::uuid ORDER BY symbol",
            user_id,
        )
        if not holdings:
            return ToolResult(True, "Danh mục hiện chưa có mã nào.")
        lines = [
            f"- {row['symbol']}: {row['quantity']:,.0f} CP, giá vốn {row['average_price']:,.0f}đ"
            for row in holdings
        ]
        return ToolResult(True, "Danh mục hiện tại:\n" + "\n".join(lines))

    reminder_match = re.match(
        r"^(?:nhắc|nhac)\s+(\d+(?:p|ph|phut|h|gio|ngay|d)|[01]?\d|2[0-3]):[0-5]\d\s+(.+)$",
        text.strip(),
        re.I,
    )
    if reminder_match:
        spec = text.strip().split(maxsplit=2)[1]
        content = reminder_match.group(2).strip()
        return ToolResult(True, await reminders.add_reminder(user_id, spec, content))

    return ToolResult(False)
