from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import services.zalo_digest as zalo_digest
from core.models import Channel, Role, User

_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def test_seconds_until_next_run_same_day(monkeypatch):
    monkeypatch.setattr(
        zalo_digest, "settings", replace(zalo_digest.settings, zalo_daily_digest_hour=21)
    )
    now = datetime(2026, 8, 7, 10, 0, 0, tzinfo=_TZ)
    seconds = zalo_digest.seconds_until_next_run(now)
    assert seconds == 11 * 3600


def test_seconds_until_next_run_rolls_to_tomorrow(monkeypatch):
    monkeypatch.setattr(
        zalo_digest, "settings", replace(zalo_digest.settings, zalo_daily_digest_hour=21)
    )
    now = datetime(2026, 8, 7, 22, 0, 0, tzinfo=_TZ)
    seconds = zalo_digest.seconds_until_next_run(now)
    assert seconds == 23 * 3600


async def test_run_daily_digest_skips_when_no_admin(monkeypatch):
    async def fake_admin_user():
        return None

    monkeypatch.setattr(zalo_digest.database, "zalo_admin_user", fake_admin_user)
    assert await zalo_digest.run_daily_digest() == 0


async def test_run_daily_digest_sends_for_each_group_with_messages(monkeypatch):
    admin = User("u1", Channel.ZALO, "admin-ext", Role.ZALO_ADMIN)

    async def fake_admin_user():
        return admin

    async def fake_enabled_groups():
        return [
            {"group_id": "g1", "alias": "nhom-a"},
            {"group_id": "g2", "alias": None},
        ]

    async def fake_summarize_group(user, label, period):
        assert user is admin
        assert period == "24h"
        return "Không có tin nhắn phù hợp trong nhóm được phép." if label == "g2" else "Tóm tắt g1"

    sent_calls = []

    async def fake_send_message(external_id, text):
        sent_calls.append((external_id, text))
        return True

    monkeypatch.setattr(zalo_digest.database, "zalo_admin_user", fake_admin_user)
    monkeypatch.setattr(zalo_digest.database, "zalo_enabled_groups", fake_enabled_groups)
    monkeypatch.setattr(zalo_digest, "summarize_group", fake_summarize_group)
    monkeypatch.setattr(zalo_digest, "send_message", fake_send_message)

    sent = await zalo_digest.run_daily_digest()

    assert sent == 1
    assert sent_calls == [("admin-ext", "📊 Tổng kết nhóm nhom-a (24h)\n\nTóm tắt g1")]
