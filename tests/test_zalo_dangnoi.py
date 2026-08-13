import channels.zalo as zalo_channel
from core.models import Channel, Role, User


async def test_today_discussion_denies_non_admin():
    normal = User("2", Channel.ZALO, "user", Role.USER)
    result = await zalo_channel.today_discussion(normal, "vip")
    assert "quản trị viên" in result


async def test_today_discussion_reports_group_not_found(monkeypatch):
    admin = User("1", Channel.ZALO, "admin", Role.ZALO_ADMIN)

    async def fake_group_id_for(alias):
        assert alias == "vip"
        return None

    monkeypatch.setattr(zalo_channel.database, "zalo_group_id_for", fake_group_id_for)
    result = await zalo_channel.today_discussion(admin, "vip")
    assert "Không tìm thấy nhóm" in result
