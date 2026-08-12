import services.zoom_admin as zoom_admin


async def test_empty_external_id_rejected_without_touching_db():
    assert "Cú pháp" in await zoom_admin.pair("", "")
    assert "Cú pháp" in await zoom_admin.lock("")
    assert "Cú pháp" in await zoom_admin.unlock("")
    assert "Cú pháp" in await zoom_admin.remove("")


async def test_list_users_empty(monkeypatch):
    async def fake_list_users():
        return []

    monkeypatch.setattr(zoom_admin.database, "zoom_list_users", fake_list_users)
    assert await zoom_admin.list_users() == "Chưa có Zoom user nào được pair."


async def test_list_users_formats_status(monkeypatch):
    async def fake_list_users():
        return [
            {
                "external_id": "u1@xmpp.zoom.us",
                "display_name": "",
                "status": "suspended",
                "paired_at": None,
                "last_active_at": None,
            }
        ]

    monkeypatch.setattr(zoom_admin.database, "zoom_list_users", fake_list_users)
    result = await zoom_admin.list_users()
    assert "👤 u1@xmpp.zoom.us (không tên) — đã khóa" in result


async def test_lock_reports_not_found(monkeypatch):
    async def fake_set_status(external_id, status):
        return False

    monkeypatch.setattr(zoom_admin.database, "zoom_set_status", fake_set_status)
    result = await zoom_admin.lock("ghost")
    assert "Không tìm thấy" in result


async def test_pair_reports_name_actually_saved(monkeypatch):
    """Regression: zoom_pair có thể giữ lại tên cũ khi gọi lại không kèm tên mới (xem
    core/database.py::zoom_pair). Message trả về phải phản ánh tên thật đã lưu."""

    async def fake_zoom_pair(external_id, display_name):
        return object(), "Tên Cũ Đã Lưu"

    monkeypatch.setattr(zoom_admin.database, "zoom_pair", fake_zoom_pair)
    result = await zoom_admin.pair("id123", "")
    assert "Tên Cũ Đã Lưu" in result
    assert "không tên" not in result
