import services.commands as commands
from bot import ZALO_ADMIN_COMMANDS, _bot_commands, _start_text


def test_bot_commands_include_every_registered_command():
    names = {c.command for c in _bot_commands()}
    for cmd in commands.COMMANDS:
        assert cmd.lstrip("/") in names
    for cmd in ZALO_ADMIN_COMMANDS:
        assert cmd in names
    assert {"start", "help", "zalodanhsach", "zalologin", "kbreindex"} <= names


def test_bot_commands_have_no_duplicates_and_valid_names():
    seen = set()
    for entry in _bot_commands():
        assert entry.command not in seen, f"trùng lệnh {entry.command} trong menu"
        seen.add(entry.command)
        assert entry.command == entry.command.lower()
        assert " " not in entry.command
        assert 1 <= len(entry.description) <= 256


def test_bot_commands_puts_frequent_commands_first():
    names = [c.command for c in _bot_commands()]
    assert names[0] == "help"
    assert names.index("stock") < names.index("zalopair")


def test_start_text_mentions_help_tip_via_same_content_as_help():
    # /help dùng chung nội dung với /start (xem bot.help_command) — đảm bảo nội
    # dung này thật sự tồn tại và không rỗng.
    text = _start_text()
    assert "/stock" in text
    assert text.strip() != ""
