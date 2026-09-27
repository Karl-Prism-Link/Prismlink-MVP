from services.voice.contact import is_explicit_affirmation, is_explicit_rejection
from services.voice.model_router import (
    ModelTier,
    confirmation_fastpath_action,
    route_voice_turn,
)
from services.voice.speech_cleanup import booking_success_speech


def decision(**overrides):
    values = {
        "intent": "unknown",
        "service": None,
        "starts_at": None,
        "last_tool": None,
        "last_tool_ok": None,
        "confirmed": False,
        "latest_user_text": "hello",
        "after_tool_result": False,
    }
    values.update(overrides)
    return route_voice_turn(**values)


def test_social_turn_uses_fast_model_without_tools():
    result = decision(latest_user_text="Thanks")
    assert result.tier is ModelTier.FAST
    assert result.use_tools is False


def test_booking_date_time_uses_reasoning_model():
    result = decision(
        intent="booking",
        service="Men's Cut",
        latest_user_text="Monday at two o'clock",
    )
    assert result.tier is ModelTier.REASONING
    assert result.use_tools is True
    assert result.reason == "booking_date_time"


def test_resolved_service_short_reply_can_use_fast_model():
    result = decision(
        intent="booking",
        service="Men's Cut",
        latest_user_text="means cut",
    )
    assert result.tier is ModelTier.FAST
    assert result.use_tools is True


def test_management_always_uses_reasoning_model():
    result = decision(intent="cancel", latest_user_text="cancel my appointment")
    assert result.tier is ModelTier.REASONING
    assert result.use_tools is True


def test_tool_continuation_cannot_chain_another_tool():
    result = decision(
        intent="booking",
        last_tool="check_availability",
        last_tool_ok=True,
        latest_user_text="Wednesday at nine",
        after_tool_result=True,
    )
    assert result.tier is ModelTier.FAST
    assert result.use_tools is False


def test_tool_failure_continuation_uses_reasoning_without_more_tools():
    result = decision(
        intent="booking",
        last_tool="check_availability",
        last_tool_ok=False,
        latest_user_text="Wednesday at nine",
        after_tool_result=True,
    )
    assert result.tier is ModelTier.REASONING
    assert result.use_tools is False


def test_prepared_booking_bare_yes_is_deterministic():
    text = "Yes."
    action = confirmation_fastpath_action(
        latest_user_text=text,
        has_prepared_booking=True,
        has_prepared_reschedule=False,
        has_prepared_cancel=False,
        is_affirmation=is_explicit_affirmation(text),
        is_rejection=is_explicit_rejection(text),
    )
    assert action == "booking_confirm"


def test_prepared_reschedule_bare_no_is_deterministic():
    text = "No."
    action = confirmation_fastpath_action(
        latest_user_text=text,
        has_prepared_booking=False,
        has_prepared_reschedule=True,
        has_prepared_cancel=False,
        is_affirmation=is_explicit_affirmation(text),
        is_rejection=is_explicit_rejection(text),
    )
    assert action == "reschedule_reject"


def test_mixed_no_with_new_time_is_not_swallowed_by_fastpath():
    text = "No, make it four."
    action = confirmation_fastpath_action(
        latest_user_text=text,
        has_prepared_booking=False,
        has_prepared_reschedule=True,
        has_prepared_cancel=False,
        is_affirmation=is_explicit_affirmation(text),
        is_rejection=is_explicit_rejection(text),
    )
    assert action is None


def test_booking_success_speech_uses_authoritative_result():
    assert (
        booking_success_speech(
            {"service": "Men's Cut", "spoken_start": "Monday 17 August 2026 at 2:00 PM"}
        )
        == "Your Men's Cut is booked for Monday 17 August 2026 at 2:00 PM."
    )


def test_mixed_no_with_bare_number_word_is_not_swallowed_by_fastpath():
    text = "No, three."
    action = confirmation_fastpath_action(
        latest_user_text=text,
        has_prepared_booking=False,
        has_prepared_reschedule=True,
        has_prepared_cancel=False,
        is_affirmation=is_explicit_affirmation(text),
        is_rejection=is_explicit_rejection(text),
    )
    assert action is None
