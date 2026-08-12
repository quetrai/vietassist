from bot import ZOOM_ADMIN_COMMANDS, call_zoom_admin_handler, parse_zalo_admin_args


def test_parse_id_and_name():
    assert parse_zalo_admin_args("/zoompair abc123 Nguyễn Văn A") == (
        "abc123",
        "Nguyễn Văn A",
    )


def test_parse_id_only():
    assert parse_zalo_admin_args("/zoomkhoa abc123") == ("abc123", "")


def test_parse_no_args():
    assert parse_zalo_admin_args("/zoomxoa") == ("", "")


def test_all_zoom_admin_commands_registered():
    assert set(ZOOM_ADMIN_COMMANDS) == {"zoompair", "zoomkhoa", "zoommokhoa", "zoomxoa"}


async def test_call_zoom_admin_handler_passes_correct_arity(monkeypatch):
    """Regression: lock/unlock/remove chỉ nhận 1 tham số, khác pair nhận 2."""
    calls: dict[str, tuple] = {}

    def make_fake(cmd, arity):
        async def fake(*args):
            assert len(args) == arity
            calls[cmd] = args
            return f"{cmd} ok"

        return fake

    monkeypatch.setitem(ZOOM_ADMIN_COMMANDS, "zoompair", make_fake("zoompair", 2))
    for cmd in ("zoomkhoa", "zoommokhoa", "zoomxoa"):
        monkeypatch.setitem(ZOOM_ADMIN_COMMANDS, cmd, make_fake(cmd, 1))

    for cmd in ZOOM_ADMIN_COMMANDS:
        result = await call_zoom_admin_handler(cmd, "id123", "Tên A")
        assert result == f"{cmd} ok"

    assert calls["zoompair"] == ("id123", "Tên A")
    assert calls["zoomkhoa"] == ("id123",)
