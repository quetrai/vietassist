from dataclasses import replace

import services.zalo_push as zalo_push


async def test_send_message_noop_when_zalo_disabled(monkeypatch):
    monkeypatch.setattr(zalo_push, "settings", replace(zalo_push.settings, zalo_enabled=False))
    result = await zalo_push.send_message("ext-1", "hello")
    assert result is False
