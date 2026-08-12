import web
from channels.zoom import ZoomEvent


def _event(**overrides) -> ZoomEvent:
    base = dict(
        event_id="e1", sender_jid="stranger1@xmpp.zoom.us", text="chào bạn", to_jid="bot@xmpp.zoom.us"
    )
    base.update(overrides)
    return ZoomEvent(**base)


async def test_unpaired_sender_gets_no_auto_reply(monkeypatch):
    """Chưa pair phải im lặng với sender (không lộ ra là bot chưa cấu hình xong) — chỉ báo
    ngầm cho Telegram owner, giống nguyên tắc của kênh Zalo."""

    async def fake_resolve_zoom_user(sender_jid):
        return None

    sent_messages = []

    async def fake_send_zoom_message(to_jid, text):
        sent_messages.append((to_jid, text))

    notified = {}

    async def fake_notify(sender_jid):
        notified["sender_jid"] = sender_jid

    monkeypatch.setattr(web, "resolve_zoom_user", fake_resolve_zoom_user)
    monkeypatch.setattr(web, "send_zoom_message", fake_send_zoom_message)
    monkeypatch.setattr(web, "_notify_owner_unpaired_zoom_sender", fake_notify)

    await web._handle_zoom_event(_event())

    assert sent_messages == []
    assert notified["sender_jid"] == "stranger1@xmpp.zoom.us"
