import asyncpg

import services.zalo_groups as zalo_groups


async def test_add_group_empty_id_rejected_without_db():
    assert "Cú pháp" in await zalo_groups.add_group("", "")


async def test_remove_group_empty_id_rejected_without_db():
    assert "Cú pháp" in await zalo_groups.remove_group("")


async def test_list_groups_empty(monkeypatch):
    async def fake_list_groups():
        return []

    monkeypatch.setattr(zalo_groups.database, "zalo_list_groups", fake_list_groups)
    result = await zalo_groups.list_groups()
    assert "Chưa có nhóm" in result


async def test_list_groups_formats_enabled_and_alias(monkeypatch):
    async def fake_list_groups():
        return [
            {"group_id": "g1", "alias": "nhom-vip", "enabled": True, "created_at": None},
            {"group_id": "g2", "alias": None, "enabled": False, "created_at": None},
        ]

    monkeypatch.setattr(zalo_groups.database, "zalo_list_groups", fake_list_groups)
    result = await zalo_groups.list_groups()
    assert "g1 (alias: nhom-vip) — bật" in result
    assert "g2 — tắt" in result


async def test_add_group_success(monkeypatch):
    calls = []

    async def fake_enable_group(group_id, alias):
        calls.append((group_id, alias))

    monkeypatch.setattr(zalo_groups.database, "zalo_enable_group", fake_enable_group)
    result = await zalo_groups.add_group("g1", "nhom-vip")
    assert calls == [("g1", "nhom-vip")]
    assert "Đã thêm nhóm g1" in result and "nhom-vip" in result


async def test_add_group_duplicate_alias_reports_friendly_error(monkeypatch):
    async def fake_enable_group(group_id, alias):
        raise asyncpg.UniqueViolationError("duplicate alias")

    monkeypatch.setattr(zalo_groups.database, "zalo_enable_group", fake_enable_group)
    result = await zalo_groups.add_group("g1", "nhom-vip")
    assert "đã được dùng" in result


async def test_remove_group_not_found(monkeypatch):
    async def fake_disable_group(identifier):
        return False

    monkeypatch.setattr(zalo_groups.database, "zalo_disable_group", fake_disable_group)
    result = await zalo_groups.remove_group("ghost")
    assert "Không tìm thấy" in result
