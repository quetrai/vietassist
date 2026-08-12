import web
from channels.zalo import ZaloEvent


def _event(**overrides) -> ZaloEvent:
    base = dict(event_id="e1", sender_id="stranger1", text="chào bạn", kind="direct")
    base.update(overrides)
    return ZaloEvent(**base)


async def test_unpaired_sender_gets_no_auto_reply(monkeypatch):
    """Zalo B chưa pair phải im lặng như tài khoản Zalo bình thường — không được
    tự động trả lời người lạ để lộ ra là bot."""

    async def fake_resolve_user(sender_id):
        return None

    notified = {}

    async def fake_notify(sender_id, sender_name):
        notified["sender_id"] = sender_id

    monkeypatch.setattr(web, "resolve_user", fake_resolve_user)
    monkeypatch.setattr(web, "_notify_owner_unpaired_sender", fake_notify)

    result = await web._handle_zalo_event(_event())

    assert result.messages == []
    assert notified["sender_id"] == "stranger1"
