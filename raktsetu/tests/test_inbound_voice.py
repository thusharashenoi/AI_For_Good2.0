"""Tests for natural inbound voice greetings."""
from shared.voice_speech import INBOUND_VOICE_RULES, build_inbound_greeting


def test_inbound_greeting_not_robotic_menu():
    msg = build_inbound_greeting()
    assert "IVR" not in msg
    assert "after the beep" not in msg.lower()
    assert "donor" in msg.lower() or "register" in msg.lower()
    assert "blood" in msg.lower()


def test_inbound_rules_discourage_menu():
    assert "IVR" in INBOUND_VOICE_RULES
    assert "phone menu" in INBOUND_VOICE_RULES.lower() or "IVR menu" in INBOUND_VOICE_RULES
