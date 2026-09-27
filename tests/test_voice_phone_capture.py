from services.voice.contact import (
    extract_phone_from_speech,
    is_explicit_affirmation,
    phone_from_recent_transcripts,
    speak_phone_digits,
)


def test_spoken_phone_digits_are_captured_without_invention():
    capture = extract_phone_from_speech("Tom, o seven eight six five three four five five.")
    assert capture is not None
    assert capture.digits == "078653455"
    assert len(capture.digits) == 9
    assert capture.spoken == "zero seven eight six five three four five five"


def test_numeric_phone_is_captured_and_time_is_not():
    assert extract_phone_from_speech("Monday at 02:00") is None
    capture = extract_phone_from_speech("My number is 021 123 4567")
    assert capture is not None
    assert capture.digits == "0211234567"


def test_international_nz_prefix_normalizes_to_local_zero():
    capture = extract_phone_from_speech("plus six four two one one two three four five six seven")
    assert capture is not None
    assert capture.digits == "0211234567"


def test_recent_transcripts_use_latest_phone_bearing_turn():
    capture = phone_from_recent_transcripts(
        ("Monday at 02:00", "Tom, zero two one one two three four five six seven", "Yes")
    )
    assert capture is not None
    assert capture.digits == "0211234567"


def test_explicit_affirmation_rejects_negation():
    assert is_explicit_affirmation("Yes") is True
    assert is_explicit_affirmation("That's correct") is True
    assert is_explicit_affirmation("No, that's not right") is False


def test_phone_readback_is_digit_by_digit():
    assert speak_phone_digits("021") == "zero two one"
