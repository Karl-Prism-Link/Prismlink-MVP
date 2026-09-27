from services.service_names import configured_service_name_from_speech
from services.voice.contact import recent_booking_datetime_phrase


def test_service_clarification_recovers_previous_full_datetime_turn():
    turns = (
        "Can I make an appointment for a Cut? On Thursday, at 03:00?",
        "A means cut.",
    )
    assert recent_booking_datetime_phrase(turns) == turns[0]
    assert (
        configured_service_name_from_speech(
            turns[1], ("Women's Cut", "Men's Cut", "Blow Wave", "Colour")
        )
        == "Men's Cut"
    )


def test_datetime_context_combines_split_day_and_time_without_calculating_date():
    turns = ("Thursday please.", "At 3 o'clock.", "A means cut.")
    phrase = recent_booking_datetime_phrase(turns)
    assert phrase == "Thursday please. At 3 o'clock."
    assert "2026" not in phrase


def test_service_only_reply_without_datetime_context_does_not_invent_time():
    turns = ("I want to book a cut.", "A means cut.")
    assert recent_booking_datetime_phrase(turns) is None
