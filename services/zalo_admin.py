from __future__ import annotations

from core import database

_ROLE_ICON = {"admin": "👑", "user": "👤"}
_STATUS_LABEL = {"active": "hoạt động", "suspended": "đã khóa"}


async def pair(external_id: str, display_name: str) -> str:
    if not external_id:
        return "Cú pháp: /zalopair <id_zalo> [tên hiển thị]"
    _, saved_name = await database.zalo_pair(external_id, display_name)
    ten = saved_name or "không tên"
    return f"Đã pair {external_id} ({ten}) — quyền: user."


async def set_admin(external_id: str, display_name: str) -> str:
    if not external_id:
        return "Cú pháp: /zaloadmin <id_zalo> [tên hiển thị]"
    await database.zalo_set_admin(external_id, display_name)
    return f"Đã đặt {external_id} làm admin duy nhất. Admin cũ (nếu có) đã hạ về user."


async def lock(external_id: str) -> str:
    if not external_id:
        return "Cú pháp: /zalokhoa <id_zalo>"
    found = await database.zalo_set_status(external_id, "suspended")
    return f"Đã khóa {external_id}." if found else f"Không tìm thấy {external_id} đã pair."


async def unlock(external_id: str) -> str:
    if not external_id:
        return "Cú pháp: /zalomokhoa <id_zalo>"
    found = await database.zalo_set_status(external_id, "active")
    return f"Đã mở khóa {external_id}." if found else f"Không tìm thấy {external_id} đã pair."


async def remove(external_id: str) -> str:
    if not external_id:
        return "Cú pháp: /zaloxoa <id_zalo>"
    found = await database.zalo_delete(external_id)
    if found:
        return f"Đã xóa pairing {external_id}. Lịch sử chat/danh mục vẫn được giữ."
    return f"Không tìm thấy {external_id} đã pair."


async def list_users() -> str:
    rows = await database.zalo_list_users()
    if not rows:
        return "Chưa có Zalo user nào được pair."
    lines = [_format_row(row) for row in rows]
    return "\n".join(lines)


def _format_row(row: dict[str, object]) -> str:
    icon = _ROLE_ICON.get(str(row["role"]), "👤")
    status = _STATUS_LABEL.get(str(row["status"]), str(row["status"]))
    ten = row["display_name"] or "không tên"
    return f"{icon} {row['external_id']} ({ten}) — {status}"
