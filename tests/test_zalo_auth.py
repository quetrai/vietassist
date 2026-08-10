from channels.zalo import is_group_command
from core.models import Channel, Role, User


def test_only_admin_can_summarize_groups():
    admin = User("1", Channel.ZALO, "admin", Role.ZALO_ADMIN)
    normal = User("2", Channel.ZALO, "user", Role.USER)
    assert admin.can_use_group_summary
    assert not normal.can_use_group_summary


def test_group_command_detection():
    assert is_group_command("/tongket room 24h")
    assert is_group_command("/nhom")
    assert not is_group_command("xin chào")


def test_group_command_empty_text_does_not_crash():
    assert not is_group_command("")
    assert not is_group_command("   ")
