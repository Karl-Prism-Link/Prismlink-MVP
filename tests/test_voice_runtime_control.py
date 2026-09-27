from services.voice.runtime_control import (
    STATE_MARKER,
    compact_context_messages,
    is_generic_booking_request,
    is_rate_limit_error,
    parse_retry_delay_seconds,
)
from services.voice.session import VoiceConversationState


def test_parse_retry_delay_from_groq_milliseconds():
    assert parse_retry_delay_seconds("Please try again in 877.5ms") == 0.8775


def test_rate_limit_detection():
    assert is_rate_limit_error("Error code: 429 - rate_limit_exceeded") is True
    assert is_rate_limit_error("calendar conflict") is False


def test_compact_context_keeps_recent_user_turns_and_replaces_state():
    messages = [
        {"role": "developer", "content": "caller-id-hint"},
        {"role": "developer", "content": f"{STATE_MARKER} old"},
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "Monday"},
        {"role": "assistant", "content": "What time?"},
        {"role": "user", "content": "Two o'clock"},
        {"role": "assistant", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "available"},
        {"role": "assistant", "content": "That time is free."},
    ]

    result = compact_context_messages(messages, max_user_turns=2, state_summary="service=Men's Cut")

    assert result[0]["content"] == "caller-id-hint"
    assert result[1]["content"].startswith(STATE_MARKER)
    assert all(message.get("content") != "old question" for message in result)
    assert any(message.get("content") == "Monday" for message in result)
    assert any(message.get("role") == "tool" for message in result)


def test_voice_state_is_compact_and_structured():
    state = VoiceConversationState(service="Men's Cut", caller_time_phrase="Monday at two o'clock")
    summary = state.summary()
    assert "service=Men's Cut" in summary
    assert "time_phrase=Monday at two o'clock" in summary
    assert "confirmed=false" in summary


def test_generic_booking_request_covers_common_stt_variants():
    positives = (
        "Can I book an appointment?",
        "I book in an appointment?",
        "Can I make a booking?",
        "Book me in please.",
        "I want to book.",
        "I need an appointment.",
    )
    for utterance in positives:
        assert is_generic_booking_request(utterance) is True, utterance


def test_generic_booking_request_does_not_steal_management_intents():
    negatives = (
        "I need to cancel my appointment.",
        "Can I reschedule my appointment?",
        "I want to move my appointment.",
        "I need to change my booking.",
    )
    for utterance in negatives:
        assert is_generic_booking_request(utterance) is False, utterance
