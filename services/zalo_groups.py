from __future__ import annotations

import asyncpg

from core import database


async def list_groups() -> str:
    rows = await database.zalo_list_groups()
    if not rows:
        return (
            "Chưa có nhóm nào được ghi nhận. Nhắn thử trong nhóm để B nhận diện, "
            "hoặc /themnhom <group_id> [alias] nếu đã biết ID."
        )
    return "\n".join(_format_group(row) for row in rows)


def _format_group(row: dict[str, object]) -> str:
    trang_thai = "bật" if row["enabled"] else "tắt"
    # Ưu tiên alias admin tự đặt (/themnhom); nếu chưa có, dùng tên nhóm thật lấy
    # tự động từ Zalo (group_name, xem zalo_register_group) để vẫn hiện tên thân
    # thiện thay vì group_id trần.
    alias = row.get("alias")
    group_name = row.get("group_name")
    if alias:
        label = f" (alias: {alias})"
    elif group_name:
        label = f" (tên: {group_name})"
    else:
        label = ""
    return f"{row['group_id']}{label} — {trang_thai}"


async def add_group(group_id: str, alias: str) -> str:
    if not group_id:
        return "Cú pháp: /themnhom <group_id> [alias]"
    try:
        await database.zalo_enable_group(group_id, alias or None)
    except asyncpg.UniqueViolationError:
        return f"Alias '{alias}' đã được dùng cho nhóm khác."
    ten = f" với alias '{alias}'" if alias else ""
    return (
        f"Đã thêm nhóm {group_id} vào allowlist{ten}.\n"
        "⚠️ Lưu ý: từ giờ, tin nhắn của MỌI thành viên trong nhóm này (kể cả người chưa "
        "được pair dùng bot) sẽ được lưu lại để phục vụ /tongket. Đảm bảo các thành viên "
        "trong nhóm biết và đồng ý trước khi bật tính năng này."
    )


async def remove_group(identifier: str) -> str:
    if not identifier:
        return "Cú pháp: /xoanhom <group_id hoặc alias>"
    found = await database.zalo_disable_group(identifier)
    if found:
        return f"Đã gỡ {identifier} khỏi allowlist. Tin nhắn đã thu thập trước đó vẫn được giữ."
    return f"Không tìm thấy nhóm {identifier}."
