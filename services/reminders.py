from __future__ import annotations

import asyncio
import logging
import re
from contextlib import suppress
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core import database
from core.config import settings
from core.models import Channel
from services.zalo_push import send_message

logger = logging.getLogger(__name__)
_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

_REL_UNITS = {
    "p": timedelta(minutes=1),
    "ph": timedelta(minutes=1),
    "phut": timedelta(minutes=1),
    "h": timedelta(hours=1),
    "gio": timedelta(hours=1),
    "ngay": timedelta(days=1),
    "d": timedelta(days=1),
}
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_REL_RE = re.compile(r"^(\d+)(p|ph|phut|h|gio|ngay|d)$")


def parse_when(spec: str, now: datetime | None = None) -> datetime | None:
    """Hiểu HH:MM (giờ VN, qua ngày hôm sau nếu đã trôi qua) hoặc số+đơn vị (30p, 2h, 1ngay)."""
    now = now or datetime.now(_TZ)
    spec = spec.strip().lower()
    rel = _REL_RE.match(spec)
    if rel:
        return now + _REL_UNITS[rel.group(2)] * int(rel.group(1))
    abs_match = _TIME_RE.match(spec)
    if abs_match:
        target = now.replace(
            hour=int(abs_match.group(1)), minute=int(abs_match.group(2)), second=0, microsecond=0
        )
        if target <= now:
            target += timedelta(days=1)
        return target
    return None


async def add_note(user_id: str, content: str) -> str:
    content = content.strip()
    if not content:
        return "Cú pháp: /ghichu <nội dung>"
    note_id = await database.add_note(user_id, content)
    return f"Đã lưu ghi chú #{note_id}."


async def list_notes(user_id: str) -> str:
    rows = await database.list_notes(user_id)
    if not rows:
        return "Chưa có ghi chú nào."
    return "\n".join(f"#{row['id']}: {row['content']}" for row in rows)


async def remove_note(user_id: str, raw_id: str) -> str:
    if not raw_id.isdigit():
        return "Cú pháp: /xoaghichu <id>"
    found = await database.delete_note(user_id, int(raw_id))
    return f"Đã xóa ghi chú #{raw_id}." if found else f"Không tìm thấy ghi chú #{raw_id}."


async def add_reminder(user_id: str, spec: str, content: str) -> str:
    content = content.strip()
    if not spec or not content:
        return "Cú pháp: /nhac <thời gian> <nội dung> (vd: /nhac 30p uống thuốc, /nhac 14:00 họp)"
    remind_at = parse_when(spec)
    if remind_at is None:
        return "Không hiểu thời gian. Dùng HH:MM, hoặc số+đơn vị p/h/ngay (vd: 30p, 2h, 1ngay)."
    reminder_id = await database.add_reminder(user_id, content, remind_at)
    local = remind_at.astimezone(_TZ)
    return f"Đã đặt nhắc nhở #{reminder_id} lúc {local:%H:%M %d/%m}."


async def list_reminders(user_id: str) -> str:
    rows = await database.list_reminders(user_id)
    if not rows:
        return "Chưa có nhắc nhở nào."
    lines = []
    for row in rows:
        local = row["remind_at"].astimezone(_TZ)
        lines.append(f"#{row['id']} lúc {local:%H:%M %d/%m}: {row['content']}")
    return "\n".join(lines)


async def remove_reminder(user_id: str, raw_id: str) -> str:
    if not raw_id.isdigit():
        return "Cú pháp: /xoanhac <id>"
    found = await database.delete_reminder(user_id, int(raw_id))
    return f"Đã hủy nhắc nhở #{raw_id}." if found else f"Không tìm thấy nhắc nhở #{raw_id}."


async def deliver_due_reminders(telegram_bot: object) -> int:
    rows = await database.claim_due_reminders()
    delivered = 0
    for row in rows:
        text = f"⏰ Nhắc nhở: {row['content']}"
        try:
            if row["channel"] == Channel.TELEGRAM.value:
                await telegram_bot.send_message(chat_id=int(row["external_id"]), text=text)
            else:
                ok = await send_message(str(row["external_id"]), text)
                if not ok:
                    raise RuntimeError("Zalo gateway không phản hồi")
        except Exception:
            if row["attempts"] >= database.REMINDER_MAX_ATTEMPTS:
                await database.mark_reminder_failed(row["id"], "delivery attempts exhausted")
                logger.error(
                    "Reminder #%s bỏ cuộc sau %d lần gửi lỗi: %s",
                    row["id"],
                    row["attempts"],
                    row["content"],
                )
                await _notify_owner_gave_up(telegram_bot, row)
            else:
                await database.release_reminder(row["id"], "delivery failed")
                logger.exception(
                    "Reminder #%s delivery failed (attempt %d/%d)",
                    row["id"],
                    row["attempts"],
                    database.REMINDER_MAX_ATTEMPTS,
                )
            continue
        await database.mark_reminder_sent(row["id"])
        delivered += 1
    return delivered


async def _notify_owner_gave_up(telegram_bot: object, row: dict[str, object]) -> None:
    if not settings.telegram_owner_id:
        return
    text = (
        f"⚠️ Nhắc nhở #{row['id']} gửi lỗi {row['attempts']} lần liên tiếp, đã dừng thử lại: "
        f"{row['content']}"
    )
    with suppress(Exception):
        await telegram_bot.send_message(chat_id=settings.telegram_owner_id, text=text)


async def reminder_loop(telegram_bot: object) -> None:
    while True:
        await asyncio.sleep(30)
        try:
            await deliver_due_reminders(telegram_bot)
        except Exception:
            logger.exception("Reminder loop lỗi")
