from services.service_names import configured_service_name_from_speech
from services.voice.contact import extract_booking_day_reference, has_explicit_booking_time
from services.voice.speech_cleanup import is_spoken_question, spoken_segment_key


def test_configured_service_resolver_handles_known_nz_stt_confusions():
    configured = ("Women's Cut", "Men's Cut", "Blow Wave", "Colour")
    assert configured_service_name_from_speech("a beans cut", configured) == "Men's Cut"
    assert configured_service_name_from_speech("A Meme's Cut", configured) == "Men's Cut"
    assert configured_service_name_from_speech("mean cat", configured) == "Men's Cut"


def test_custom_configured_service_exact_match_wins_over_generic_confusion():
    configured = ("Bean's Cut", "Men's Cut")
    assert configured_service_name_from_speech("I want a Bean's Cut", configured) == "Bean's Cut"


def test_partial_day_reference_preserves_words_without_calculating_date():
    assert extract_booking_day_reference("On Thursday") == "Thursday"
    assert extract_booking_day_reference("next monday please") == "next Monday"
    assert has_explicit_booking_time("On Thursday") is False
    assert has_explicit_booking_time("Thursday at 11AM") is True
    assert has_explicit_booking_time("Thursday 10:00") is True


def test_semantic_time_questions_share_one_dedupe_key():
    variants = [
        "What time on Thursday would you like?",
        "What time on Thursday would you prefer?",
        "Could you let me know a suitable time on Thursday?",
        "Sure, what time on Thursday works for you?",
        "Do you have a preferred time on Thursday?",
    ]
    keys = {spoken_segment_key(value) for value in variants}
    assert keys == {"ask_time:thursday"}


def test_punctuation_only_tts_segments_have_no_spoken_key():
    assert spoken_segment_key("..") == ""
    assert spoken_segment_key(".") == ""


def test_question_detection_supports_missing_question_mark():
    assert is_spoken_question("Could you confirm the exact service you'd like") is True
    assert is_spoken_question("Great, that slot is free.") is False
