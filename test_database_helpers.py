from core import database
from core.models import Channel, Role


def test_rowcount_parses_delete_result():
    assert database._rowcount("DELETE 42") == 42


def test_rowcount_zero_rows():
    assert database._rowcount("DELETE 0") == 0


def test_rowcount_unexpected_format_returns_zero():
    assert database._rowcount("bogus") == 0


def test_zoom_user_from_rows_is_always_zoom_admin():
    """Zoom chỉ dùng cho 1 người (chủ bot) nên mọi user đã pair đều được coi là
    Role.ZOOM_ADMIN ngay, không cần lệnh cấp quyền admin riêng như /zaloadmin bên
    Zalo — xem services/zoom_admin.py::pair."""
    user_row = {"id": "u1", "channel": "zoom", "external_id": "jid1"}
    zoom_row = {"status": "active"}
    user = database._zoom_user_from_rows(user_row, zoom_row)
    assert user.role == Role.ZOOM_ADMIN
    assert user.can_use_group_summary
    assert user.channel == Channel.ZOOM


def test_zoom_user_from_rows_respects_suspended_status():
    user_row = {"id": "u1", "channel": "zoom", "external_id": "jid1"}
    zoom_row = {"status": "suspended"}
    user = database._zoom_user_from_rows(user_row, zoom_row)
    assert user.active is False
    # Vẫn giữ quyền group summary dù bị khóa — active=False đã tự chặn hành động ở
    # tầng gọi (web.py kiểm tra user.active trước khi tới bất kỳ lệnh nào).
    assert user.can_use_group_summary


def test_rag_enabled_defaults_to_off():
    """RAG (knowledge base) phải mặc định TẮT cho user mới — chỉ được dùng khi user tự
    /rag on, tuyệt đối không tự bật để trả lời khi không có yêu cầu."""
    from core.models import User as _User

    assert _User("u1", Channel.ZALO, "id1", Role.USER).rag_enabled is False
