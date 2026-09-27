from pathlib import Path

from services.service_names import configured_service_name_from_speech
from services.voice.runtime_control import select_latest_caller_text
from services.voice.speech_cleanup import (
    deterministic_post_action_social_reply,
    limit_spoken_questions,
)


def test_stale_context_does_not_overwrite_newer_spelled_name_turn():
    recent = ("Cow.", "K a r l")
    assert select_latest_caller_text("Cow.", recent) == "K a r l"


def test_brand_new_context_turn_wins_when_event_callback_has_not_seen_it():
    recent = ("Cow.",)
    assert select_latest_caller_text("K a r l", recent) == "K a r l"


def test_current_context_equal_to_session_latest_is_kept():
    recent = ("Cow.", "K a r l")
    assert select_latest_caller_text("K a r l", recent) == "K a r l"


def test_additional_nz_stt_service_confusions_map_only_to_configured_mens_cut():
    configured = ("Women's Cut", "Men's Cut", "Blow Wave", "Colour")
    assert configured_service_name_from_speech("mean tea cup", configured) == "Men's Cut"
    assert configured_service_name_from_speech("means he can't", configured) == "Men's Cut"


def test_multi_question_segment_is_trimmed_after_first_question():
    text = "May I have your name, please? And your phone number? Sure, could you read it digit by digit?"
    assert limit_spoken_questions(text) == "May I have your name, please?"


def test_statement_plus_first_question_is_preserved():
    text = "Great, that slot is free. May I have your name, please? And your phone number?"
    assert limit_spoken_questions(text) == "Great, that slot is free. May I have your name, please?"


def test_post_action_thanks_has_deterministic_closing_reply():
    assert (
        deterministic_post_action_social_reply("Thank you.") == "You're welcome. Have a great day."
    )
    assert (
        deterministic_post_action_social_reply("Cool. Thank you.")
        == "You're welcome. Have a great day."
    )


def test_v044_runtime_contains_direct_datetime_and_social_fastpaths():
    source = (Path(__file__).parents[1] / "apps" / "voice" / "bot.py").read_text()
    assert "service_resolved_datetime" in source
    assert "deterministic post-action social fastpath" in source
    assert "select_latest_caller_text" in source
