from __future__ import annotations

from core import database

_STATUS_LABEL = {"active": "hoạt động", "suspended": "đã khóa"}


async def pair(external_id: str, display_name: str) -> str:
    if not external_id:
        return "Cú pháp: /zoompair <jid_zoom> [tên hiển thị]"
    _, saved_name = await database.zoom_pair(external_id, display_name)
    ten = saved_name or "không tên"
    return f"Đã pair {external_id} ({ten})."


async def lock(external_id: str) -> str:
    if not external_id:
        return "Cú pháp: /zoomkhoa <jid_zoom>"
    found = await database.zoom_set_status(external_id, "suspended")
    return f"Đã khóa {external_id}." if found else f"Không tìm thấy {external_id} đã pair."


async def unlock(external_id: str) -> str:
    if not external_id:
        return "Cú pháp: /zoommokhoa <jid_zoom>"
    found = await database.zoom_set_status(external_id, "active")
    return f"Đã mở khóa {external_id}." if found else f"Không tìm thấy {external_id} đã pair."


async def remove(external_id: str) -> str:
    if not external_id:
        return "Cú pháp: /zoomxoa <jid_zoom>"
    found = await database.zoom_delete(external_id)
    if found:
        return f"Đã xóa pairing {external_id}. Lịch sử chat/danh mục vẫn được giữ."
    return f"Không tìm thấy {external_id} đã pair."


async def list_users() -> str:
    rows = await database.zoom_list_users()
    if not rows:
        return "Chưa có Zoom user nào được pair."
    return "\n".join(_format_row(row) for row in rows)


def _format_row(row: dict[str, object]) -> str:
    status = _STATUS_LABEL.get(str(row["status"]), str(row["status"]))
    ten = row["display_name"] or "không tên"
    return f"👤 {row['external_id']} ({ten}) — {status}"
