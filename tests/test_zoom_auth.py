import dataclasses

import channels.zoom as zoom_channel
from channels.zoom import parse_event, verify_webhook_token
from core.config import settings


def test_verify_webhook_token_matches_configured_value(monkeypatch):
    monkeypatch.setattr(zoom_channel, "settings", dataclasses.replace(settings, zoom_verification_token="secret123"))
    assert verify_webhook_token("secret123")
    assert not verify_webhook_token("wrong")
    assert not verify_webhook_token("")


def test_verify_webhook_token_rejects_when_unconfigured(monkeypatch):
    monkeypatch.setattr(zoom_channel, "settings", dataclasses.replace(settings, zoom_verification_token=""))
    assert not verify_webhook_token("")
    assert not verify_webhook_token("anything")


def test_parse_event_extracts_message():
    payload = {
        "payload": {
            "userJid": "user1@xmpp.zoom.us",
            "toJid": "bot1@xmpp.zoom.us",
            "cmd": "/stock FPT",
            "messageId": "m1",
        }
    }
    event = parse_event(payload)
    assert event is not None
    assert event.sender_jid == "user1@xmpp.zoom.us"
    assert event.to_jid == "bot1@xmpp.zoom.us"
    assert event.text == "/stock FPT"
    assert event.event_id == "m1"


def test_parse_event_returns_none_when_missing_fields():
    assert parse_event({}) is None
    assert parse_event({"payload": {"userJid": "user1"}}) is None
    assert parse_event({"payload": {"cmd": "hi"}}) is None
