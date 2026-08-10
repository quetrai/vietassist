import services.zalo_admin as zalo_admin


async def test_empty_external_id_rejected_without_touching_db():
    assert "Cú pháp" in await zalo_admin.pair("", "")
    assert "Cú pháp" in await zalo_admin.set_admin("", "")
    assert "Cú pháp" in await zalo_admin.lock("")
    assert "Cú pháp" in await zalo_admin.unlock("")
    assert "Cú pháp" in await zalo_admin.remove("")


async def test_list_users_empty(monkeypatch):
    async def fake_list_users():
        return []

    monkeypatch.setattr(zalo_admin.database, "zalo_list_users", fake_list_users)
    assert await zalo_admin.list_users() == "Chưa có Zalo user nào được pair."


async def test_list_users_formats_role_and_status(monkeypatch):
    async def fake_list_users():
        return [
            {
                "external_id": "a1",
                "display_name": "Admin",
                "role": "admin",
                "status": "active",
                "paired_at": None,
                "last_active_at": None,
            },
            {
                "external_id": "u1",
                "display_name": "",
                "role": "user",
                "status": "suspended",
                "paired_at": None,
                "last_active_at": None,
            },
        ]

    monkeypatch.setattr(zalo_admin.database, "zalo_list_users", fake_list_users)
    result = await zalo_admin.list_users()
    assert "👑 a1 (Admin) — hoạt động" in result
    assert "👤 u1 (không tên) — đã khóa" in result


async def test_lock_reports_not_found(monkeypatch):
    async def fake_set_status(external_id, status):
        return False

    monkeypatch.setattr(zalo_admin.database, "zalo_set_status", fake_set_status)
    result = await zalo_admin.lock("ghost")
    assert "Không tìm thấy" in result


async def test_pair_reports_name_actually_saved(monkeypatch):
    """Regression: zalo_pair có thể giữ lại tên cũ khi gọi lại không kèm tên mới (xem
    core/database.py::zalo_pair). Message trả về phải phản ánh tên thật đã lưu, không phải
    tên rỗng vừa gõ."""

    async def fake_zalo_pair(external_id, display_name):
        return object(), "Tên Cũ Đã Lưu"

    monkeypatch.setattr(zalo_admin.database, "zalo_pair", fake_zalo_pair)
    result = await zalo_admin.pair("id123", "")
    assert "Tên Cũ Đã Lưu" in result
    assert "không tên" not in result
