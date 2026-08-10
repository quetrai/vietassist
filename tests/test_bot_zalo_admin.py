from bot import ZALO_ADMIN_COMMANDS, call_zalo_admin_handler, parse_zalo_admin_args


def test_parse_id_and_name():
    assert parse_zalo_admin_args("/zalopair abc123 Nguyễn Văn A") == (
        "abc123",
        "Nguyễn Văn A",
    )


def test_parse_id_only():
    assert parse_zalo_admin_args("/zaloadmin abc123") == ("abc123", "")


def test_parse_no_args():
    assert parse_zalo_admin_args("/zalokhoa") == ("", "")


def test_parse_extra_whitespace():
    assert parse_zalo_admin_args("/zaloxoa   abc123   ") == ("abc123", "")


def test_all_zalo_admin_commands_registered():
    assert set(ZALO_ADMIN_COMMANDS) == {
        "zalopair",
        "zaloadmin",
        "zalokhoa",
        "zalomokhoa",
        "zaloxoa",
    }


async def test_call_zalo_admin_handler_passes_correct_arity(monkeypatch):
    """Regression: lock/unlock/remove chỉ nhận 1 tham số, khác pair/set_admin nhận 2.
    Gọi sai arity sẽ raise TypeError ngay tại đây."""
    calls: dict[str, tuple] = {}

    def make_fake(cmd, arity):
        async def fake(*args):
            assert len(args) == arity
            calls[cmd] = args
            return f"{cmd} ok"

        return fake

    for cmd in ("zalopair", "zaloadmin"):
        monkeypatch.setitem(ZALO_ADMIN_COMMANDS, cmd, make_fake(cmd, 2))
    for cmd in ("zalokhoa", "zalomokhoa", "zaloxoa"):
        monkeypatch.setitem(ZALO_ADMIN_COMMANDS, cmd, make_fake(cmd, 1))

    for cmd in ZALO_ADMIN_COMMANDS:
        result = await call_zalo_admin_handler(cmd, "id123", "Tên A")
        assert result == f"{cmd} ok"

    assert calls["zalopair"] == ("id123", "Tên A")
    assert calls["zalokhoa"] == ("id123",)
