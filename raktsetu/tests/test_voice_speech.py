from shared.voice_speech import (
    format_tool_result_for_voice,
    sanitize_for_speech,
    speak_blood_group,
)


def test_blood_group_spoken():
    assert speak_blood_group("O+") == "O positive"
    assert speak_blood_group("AB-") == "AB negative"


def test_strips_markdown_and_abbreviations():
    raw = "Hello **CTO** at RaktSetu — O+ ok true!"
    out = sanitize_for_speech(raw)
    assert "CTO" not in out
    assert "true" not in out.lower()
    assert "O positive" in out
    assert "Blood Warriors" in out


def test_get_state_voice_summary():
    summary = format_tool_result_for_voice("get_state", {
        "missingForDonorRegistration": ["area", "blood_group"],
        "isReturningCaller": False,
    })
    assert "area" in summary
    assert "blood group" in summary
    assert "missingForDonorRegistration" not in summary


def test_complete_registration_voice_summary():
    summary = format_tool_result_for_voice("complete_donor_registration", {"ok": True})
    assert "Registration complete" in summary
    assert "Do not speak" in summary
    assert "{" not in summary


def test_sanitize_strips_am_pm_letters():
    out = sanitize_for_speech("See you tomorrow at 10:00 AM at the hospital.")
    assert "AM" not in out
    assert "morning" in out.lower()
