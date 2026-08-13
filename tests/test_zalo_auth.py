from channels.zalo import is_group_command
from core.models import Channel, Role, User


def test_only_admin_can_summarize_groups():
    admin = User("1", Channel.ZALO, "admin", Role.ZALO_ADMIN)
    normal = User("2", Channel.ZALO, "user", Role.USER)
    assert admin.can_use_group_summary
    assert not normal.can_use_group_summary


def test_zoom_users_are_always_group_summary_capable():
    """Zoom chỉ dùng cho 1 người nên KHÔNG phân biệt admin/user như Zalo — bất kỳ ai
    đã pair qua /zoompair (Role.ZOOM_ADMIN, gán tự động trong core/database.py::
    _zoom_user_from_rows) đều dùng được /nhom, /tongket, /dangnoi ngay."""
    zoom_user = User("3", Channel.ZOOM, "zoomjid", Role.ZOOM_ADMIN)
    assert zoom_user.can_use_group_summary


def test_group_command_detection():
    assert is_group_command("/tongket room 24h")
    assert is_group_command("/nhom")
    assert is_group_command("/dangnoi room")
    assert not is_group_command("xin chào")


def test_group_command_empty_text_does_not_crash():
    assert not is_group_command("")
    assert not is_group_command("   ")
