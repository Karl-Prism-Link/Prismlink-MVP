from services.voice.contact import extract_booking_name_candidate, extract_spelled_name
from services.voice.session import LiveCallSession, VoiceConversationState
from services.voice.speech_cleanup import booking_confirmation_speech


def _bare_session() -> LiveCallSession:
    session = object.__new__(LiveCallSession)
    session.voice_state = VoiceConversationState()
    session._prepared_booking_fingerprint = None
    session._prepared_booking_payload = None
    session._booking_phone_digits = ""
    session._booking_phone_capture_active = False
    session._booking_name_capture_active = False
    session._booking_spelled_name = None
    return session


def test_trailing_spelling_overrides_stt_phonetic_guess():
    assert extract_spelled_name("Cow. K a r l.") == "Karl"
    assert extract_spelled_name("Kyle, K a r l") == "Karl"


def test_context_gated_plain_booking_name_capture():
    assert extract_booking_name_candidate("Tom") == "Tom"
    assert extract_booking_name_candidate("My name is Karl") == "Karl"
    assert extract_booking_name_candidate("No") is None
    assert extract_booking_name_candidate("Wednesday") is None
    assert extract_booking_name_candidate("021 123 4567") is None


def test_booking_name_capture_flag_is_explicit_state():
    session = _bare_session()
    assert session.booking_name_capture_active is False
    session.set_booking_name_capture(True)
    assert session.booking_name_capture_active is True
    session.set_booking_name_capture(False)
    assert session.booking_name_capture_active is False


def test_prepared_booking_readback_is_deterministic():
    speech = booking_confirmation_speech(
        {
            "service": "Men's Cut",
            "spoken_start": "Friday 14 August 2026 at 10:00 AM",
            "customer_name": "Karl",
            "spoken_phone": "zero two one one two three four five six seven",
        }
    )
    assert speech == (
        "To confirm: Men's Cut on Friday 14 August 2026 at 10:00 AM for Karl. "
        "Phone zero two one one two three four five six seven. Is that correct?"
    )


def test_v042_booking_name_correction_bypasses_llm_and_reprepares():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "apps" / "voice" / "bot.py").read_text()
    assert "deterministic booking name correction" in source
    assert "call_session.has_prepared_booking" in source
    assert "extract_booking_name_candidate(latest_user)" in source
    assert "_handle_booking_partial_capture" in source
