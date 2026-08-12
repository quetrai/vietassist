from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from channels.zalo import summarize_group
from core import database
from core.config import settings
from services.zalo_push import send_message

logger = logging.getLogger(__name__)

_NO_MESSAGES = "Không có tin nhắn phù hợp trong nhóm được phép."
_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


async def run_daily_digest() -> int:
    """Tổng kết 24h mọi nhóm đang bật allowlist, gửi cho A admin. Trả số nhóm đã gửi."""
    admin = await database.zalo_admin_user()
    if admin is None:
        logger.info("Chưa có Zalo admin active, bỏ qua daily digest")
        return 0
    groups = await database.zalo_enabled_groups()
    sent = 0
    for group in groups:
        label = str(group["alias"] or group["group_id"])
        try:
            summary = await summarize_group(admin, label, "24h")
        except Exception:
            logger.exception("Daily digest lỗi cho nhóm %s", label)
            continue
        if summary == _NO_MESSAGES:
            continue
        ok = await send_message(admin.external_id, f"📊 Tổng kết nhóm {label} (24h)\n\n{summary}")
        sent += int(ok)
    return sent


def seconds_until_next_run(now: datetime | None = None) -> float:
    """Số giây tới lần chạy digest kế tiếp theo giờ VN (ZALO_DAILY_DIGEST_HOUR, mặc định 21h)."""
    now = now or datetime.now(_TZ)
    target = now.replace(hour=settings.zalo_daily_digest_hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def daily_digest_loop() -> None:
    """Vòng lặp vô hạn: ngủ tới đúng giờ cấu hình mỗi ngày rồi chạy run_daily_digest()."""
    while True:
        await asyncio.sleep(seconds_until_next_run())
        try:
            sent = await run_daily_digest()
            logger.info("Daily digest đã gửi cho %d nhóm", sent)
        except Exception:
            logger.exception("Daily digest loop lỗi")
