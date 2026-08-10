from __future__ import annotations

import asyncio
import logging

from core import database

logger = logging.getLogger(__name__)

_INTERVAL_SEC = 24 * 3600


async def cleanup_loop() -> None:
    """Dọn định kỳ mỗi 24h các bảng append-only không có cơ chế xoá tự nhiên (xem
    core.database.cleanup_old_data). Chạy 1 lần ngay khi khởi động rồi lặp lại."""
    while True:
        try:
            result = await database.cleanup_old_data()
            logger.info("Dọn dữ liệu cũ: %s", result)
        except Exception:
            logger.exception("Cleanup loop lỗi")
        await asyncio.sleep(_INTERVAL_SEC)
