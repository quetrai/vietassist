from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import services.reminders as reminders
from core.models import Channel

_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def test_parse_when_relative_minutes():
    now = datetime(2026, 8, 7, 10, 0, tzinfo=_TZ)
    assert reminders.parse_when("30p", now) == now + timedelta(minutes=30)


def test_parse_when_relative_hours():
    now = datetime(2026, 8, 7, 10, 0, tzinfo=_TZ)
    assert reminders.parse_when("2h", now) == now + timedelta(hours=2)


def test_parse_when_relative_days():
    now = datetime(2026, 8, 7, 10, 0, tzinfo=_TZ)
    assert reminders.parse_when("1ngay", now) == now + timedelta(days=1)


def test_parse_when_absolute_later_today():
    now = datetime(2026, 8, 7, 10, 0, tzinfo=_TZ)
    result = reminders.parse_when("14:00", now)
    assert result == now.replace(hour=14, minute=0, second=0, microsecond=0)


def test_parse_when_absolute_already_passed_rolls_to_tomorrow():
    now = datetime(2026, 8, 7, 20, 0, tzinfo=_TZ)
    result = reminders.parse_when("08:00", now)
    assert result == (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)


def test_parse_when_invalid_returns_none():
    assert reminders.parse_when("chưa hiểu") is None
    assert reminders.parse_when("25:00") is None


async def test_add_note_rejects_empty_content():
    assert "Cú pháp" in await reminders.add_note("u1", "   ")


async def test_add_note_success(monkeypatch):
    async def fake_add_note(user_id, content):
        assert user_id == "u1"
        assert content == "mua sữa"
        return 7

    monkeypatch.setattr(reminders.database, "add_note", fake_add_note)
    result = await reminders.add_note("u1", "mua sữa")
    assert result == "Đã lưu ghi chú #7."


async def test_remove_note_rejects_non_numeric_id():
    assert "Cú pháp" in await reminders.remove_note("u1", "abc")


async def test_add_reminder_rejects_missing_content():
    result = await reminders.add_reminder("u1", "30p", "")
    assert "Cú pháp" in result


async def test_add_reminder_rejects_unparseable_time():
    result = await reminders.add_reminder("u1", "lung tung", "uống thuốc")
    assert "Không hiểu thời gian" in result


async def test_add_reminder_success(monkeypatch):
    async def fake_add_reminder(user_id, content, remind_at):
        assert user_id == "u1"
        assert content == "uống thuốc"
        return 3

    monkeypatch.setattr(reminders.database, "add_reminder", fake_add_reminder)
    result = await reminders.add_reminder("u1", "30p", "uống thuốc")
    assert result.startswith("Đã đặt nhắc nhở #3")


async def test_deliver_due_reminders_routes_by_channel(monkeypatch):
    async def fake_claim_due_reminders():
        return [
            {
                "id": 1,
                "content": "A",
                "attempts": 1,
                "channel": Channel.TELEGRAM.value,
                "external_id": "123",
            },
            {
                "id": 2,
                "content": "B",
                "attempts": 1,
                "channel": Channel.ZALO.value,
                "external_id": "zalo-ext",
            },
        ]

    sent_zalo = []
    marked_sent = []

    async def fake_send_message(external_id, text):
        sent_zalo.append((external_id, text))
        return True

    async def fake_mark_reminder_sent(reminder_id):
        marked_sent.append(reminder_id)

    class FakeBot:
        def __init__(self):
            self.calls = []

        async def send_message(self, chat_id, text):
            self.calls.append((chat_id, text))

    bot = FakeBot()
    monkeypatch.setattr(reminders.database, "claim_due_reminders", fake_claim_due_reminders)
    monkeypatch.setattr(reminders.database, "mark_reminder_sent", fake_mark_reminder_sent)
    monkeypatch.setattr(reminders, "send_message", fake_send_message)

    count = await reminders.deliver_due_reminders(bot)

    assert count == 2
    assert bot.calls == [(123, "⏰ Nhắc nhở: A")]
    assert sent_zalo == [("zalo-ext", "⏰ Nhắc nhở: B")]
    assert marked_sent == [1, 2]


async def test_deliver_due_reminders_does_not_mark_sent_when_delivery_fails(monkeypatch):
    """Regression test: nếu gửi lỗi (bot ném exception, hoặc gateway Zalo trả False),
    reminder KHÔNG được đánh dấu sent, để còn được thử lại ở vòng lặp sau."""

    async def fake_claim_due_reminders():
        return [
            {
                "id": 1,
                "content": "A",
                "attempts": 1,
                "channel": Channel.TELEGRAM.value,
                "external_id": "123",
            },
            {
                "id": 2,
                "content": "B",
                "attempts": 1,
                "channel": Channel.ZALO.value,
                "external_id": "zalo-ext",
            },
        ]

    marked_sent = []

    async def fake_mark_reminder_sent(reminder_id):
        marked_sent.append(reminder_id)

    async def fake_send_message(external_id, text):
        return False  # gateway không phản hồi

    released = []

    async def fake_release_reminder(reminder_id, reason):
        released.append((reminder_id, reason))

    class FailingBot:
        async def send_message(self, chat_id, text):
            raise RuntimeError("Telegram API lỗi mạng")

    monkeypatch.setattr(reminders.database, "claim_due_reminders", fake_claim_due_reminders)
    monkeypatch.setattr(reminders.database, "mark_reminder_sent", fake_mark_reminder_sent)
    monkeypatch.setattr(reminders.database, "release_reminder", fake_release_reminder)
    monkeypatch.setattr(reminders, "send_message", fake_send_message)

    count = await reminders.deliver_due_reminders(FailingBot())

    assert count == 0
    assert marked_sent == []
    assert released == [(1, "delivery failed"), (2, "delivery failed")]


async def test_deliver_due_reminders_logs_give_up_after_max_attempts(monkeypatch, caplog):
    async def fake_claim_due_reminders():
        return [
            {
                "id": 9,
                "content": "hết lượt",
                "attempts": reminders.database.REMINDER_MAX_ATTEMPTS,
                "channel": Channel.TELEGRAM.value,
                "external_id": "123",
            }
        ]

    async def fake_mark_reminder_sent(reminder_id):
        raise AssertionError("không được mark sent khi đã bỏ cuộc")

    marked_failed = []

    async def fake_mark_reminder_failed(reminder_id, reason):
        marked_failed.append((reminder_id, reason))

    class FailingBot:
        async def send_message(self, chat_id, text):
            raise RuntimeError("lỗi mãi")

    monkeypatch.setattr(reminders.database, "claim_due_reminders", fake_claim_due_reminders)
    monkeypatch.setattr(reminders.database, "mark_reminder_sent", fake_mark_reminder_sent)
    monkeypatch.setattr(reminders.database, "mark_reminder_failed", fake_mark_reminder_failed)

    with caplog.at_level("ERROR"):
        count = await reminders.deliver_due_reminders(FailingBot())

    assert count == 0
    assert any("bỏ cuộc" in message for message in caplog.messages)
    assert marked_failed == [(9, "delivery attempts exhausted")]
