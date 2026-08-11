from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ai import router
from ai.contracts import ProviderError
from core import database

logger = logging.getLogger(__name__)
_MAX_FACTS = 40
_MAX_PENDING_UPDATES = 32
_pending_updates: set[asyncio.Task[None]] = set()


def _facts_to_text(facts: list[dict[str, str]]) -> str:
    if not facts:
        return ""
    return "\n".join(f"- {item['key']}: {item['value']}" for item in facts)


async def build_context(user_id: str) -> str:
    facts = list(await database.memory(user_id))
    normalized = [item for item in facts if isinstance(item, dict) and item.get("key") and item.get("value")]
    if not normalized:
        return ""
    return "Thông tin đã lưu về người dùng:\n" + _facts_to_text(normalized[-_MAX_FACTS:])


async def update(user_id: str, user_text: str, assistant_text: str) -> None:
    if not router.google.client:
        return
    prompt = (
        "Trích xuất thông tin bền vững về người dùng từ cuộc hội thoại dưới đây. "
        "Chỉ giữ thông tin người dùng nói hoặc xác nhận rõ ràng, hữu ích cho các cuộc trò chuyện sau. "
        "Không lưu thông tin nhạy cảm, suy đoán hoặc nội dung chỉ đúng cho phiên hiện tại. "
        "Trả về JSON thuần dạng {\"facts\":[{\"key\":\"...\",\"value\":\"...\"}]}. "
        "Nếu không có gì đáng lưu, trả facts rỗng.\n\n"
        f"User: {user_text}\nAssistant: {assistant_text}"
    )
    try:
        result = await router.google.generate_json(prompt)
        facts = result.get("facts", []) if isinstance(result, dict) else []
        if not isinstance(facts, list):
            return
        current = [item for item in await database.memory(user_id) if isinstance(item, dict)]
        merged = {str(item.get("key")): str(item.get("value")) for item in current if item.get("key")}
        for item in facts:
            if isinstance(item, dict) and item.get("key") and item.get("value"):
                merged[str(item["key"]).strip()] = str(item["value"]).strip()
        merged_items = [{"key": key, "value": value} for key, value in merged.items() if key and value]
        await database.set_memory(user_id, merged_items[-_MAX_FACTS:])
    except (ProviderError, ValueError, TypeError, json.JSONDecodeError):
        logger.debug("Memory update skipped", exc_info=True)
    except Exception:
        logger.warning("Memory update failed", exc_info=True)


def schedule_update(user_id: str, user_text: str, assistant_text: str) -> None:
    if len(_pending_updates) >= _MAX_PENDING_UPDATES:
        logger.debug("Memory update queue đầy, bỏ qua lượt cập nhật")
        return
    task = asyncio.create_task(update(user_id, user_text, assistant_text))
    _pending_updates.add(task)
    task.add_done_callback(_pending_updates.discard)


async def shutdown() -> None:
    tasks = tuple(_pending_updates)
    if not tasks:
        return
    await asyncio.gather(*tasks, return_exceptions=True)
    _pending_updates.clear()
