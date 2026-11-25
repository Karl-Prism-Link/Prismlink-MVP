from services.voice.runtime_control import (
    STATE_MARKER,
    ModelCapacityExceeded,
    RollingModelTokenBudget,
    compact_context_messages,
    is_generic_booking_request,
    is_rate_limit_error,
    parse_retry_delay_seconds,
    select_voice_tool_schemas,
)
from services.voice.session import VoiceConversationState


def test_parse_retry_delay_from_groq_milliseconds():
    assert parse_retry_delay_seconds("Please try again in 877.5ms") == 0.8775


def test_retry_delay_respects_provider_wait_beyond_three_seconds():
    assert parse_retry_delay_seconds("Please try again in 15.1425s") == 15.1425
    assert parse_retry_delay_seconds("Please try again in 877.5ms") == 0.8775
    assert parse_retry_delay_seconds("Please try again in 90s") == 30.0


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


def test_compact_context_can_fall_back_to_current_turn_only_without_losing_state():
    messages = [
        {"role": "developer", "content": "static salon policy"},
        {"role": "developer", "content": f"{STATE_MARKER} service=Women's Cut; confirmed=true"},
        {"role": "user", "content": "I want to book a haircut"},
        {"role": "assistant", "content": "Which day?"},
        {"role": "user", "content": "Wednesday"},
        {"role": "assistant", "content": "What time?"},
        {"role": "user", "content": "12 noon"},
    ]
    result = compact_context_messages(
        messages,
        max_user_turns=1,
        state_summary="service=Women's Cut; confirmed=true",
    )
    assert [m["content"] for m in result if m.get("role") == "user"] == ["12 noon"]
    assert any(m.get("content") == "static salon policy" for m in result)
    assert any("confirmed=true" in str(m.get("content")) for m in result)


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


def test_rolling_budget_tracks_models_independently():
    import asyncio

    async def run():
        budget = RollingModelTokenBudget(tokens_per_minute=1000, max_wait_seconds=0)
        await budget.reserve("model-a", 800)
        await budget.reserve("model-b", 800)
        try:
            await budget.reserve("model-a", 300)
        except ModelCapacityExceeded as exc:
            assert "model-a" in str(exc)
        else:
            raise AssertionError("model-a should have exhausted its local budget")

    asyncio.run(run())


def test_rolling_budget_rejects_single_request_larger_than_budget():
    import asyncio

    async def run():
        budget = RollingModelTokenBudget(tokens_per_minute=1000, max_wait_seconds=0)
        try:
            await budget.reserve("model-a", 1001)
        except ModelCapacityExceeded:
            return
        raise AssertionError("oversized request should be rejected before provider call")

    asyncio.run(run())


def test_request_token_estimate_includes_completion_reservation():
    budget = RollingModelTokenBudget(tokens_per_minute=8000)
    estimate = budget.estimate_request_tokens(
        {
            "messages": [{"role": "user", "content": "hello " * 320}],
            "tools": [{"type": "function", "function": {"name": "book"}}],
            "max_completion_tokens": 256,
        }
    )
    assert estimate > 256
    assert estimate >= 320


def test_local_tpm_budget_error_is_classified_for_voice_fallback():
    from services.voice.runtime_control import is_rate_limit_error

    assert is_rate_limit_error(
        "local_tpm_budget_exceeded: estimated request exceeds admission budget"
    )
    assert is_rate_limit_error("429 rate_limit_exceeded from provider")
    assert not is_rate_limit_error("invalid API key")


def test_booking_intent_exposes_only_booking_and_safe_message_tools():
    tools = [
        {"type": "function", "function": {"name": name}}
        for name in (
            "get_salon_information",
            "check_availability",
            "book_appointment",
            "find_appointments",
            "reschedule_appointment",
            "cancel_appointment",
            "take_message",
        )
    ]
    names = [
        item["function"]["name"] for item in select_voice_tool_schemas(tools, intent="booking")
    ]
    assert names == [
        "get_salon_information",
        "check_availability",
        "book_appointment",
        "take_message",
    ]
    assert "cancel_appointment" not in names
    assert "reschedule_appointment" not in names


def test_salon_enquiry_exposes_only_information_and_safe_message_tools():
    tools = [
        {"type": "function", "function": {"name": name}}
        for name in (
            "get_salon_information",
            "check_availability",
            "book_appointment",
            "find_appointments",
            "reschedule_appointment",
            "cancel_appointment",
            "take_message",
        )
    ]
    names = {
        item["function"]["name"]
        for item in select_voice_tool_schemas(
            tools, intent="unknown", routing_reason="salon_enquiry"
        )
    }
    assert names == {"get_salon_information", "take_message"}


def test_appointment_management_keeps_required_read_and_mutation_tools():
    tools = [
        {"type": "function", "function": {"name": name}}
        for name in (
            "get_salon_information",
            "check_availability",
            "book_appointment",
            "find_appointments",
            "reschedule_appointment",
            "cancel_appointment",
            "take_message",
        )
    ]
    for intent, expected in {
        "cancel": {"find_appointments", "cancel_appointment"},
        "reschedule": {"find_appointments", "check_availability", "reschedule_appointment"},
    }.items():
        names = {
            item["function"]["name"] for item in select_voice_tool_schemas(tools, intent=intent)
        }
        assert expected <= names


def test_reschedule_request_scopes_tools_even_when_session_intent_is_unknown():
    tools = [
        {"type": "function", "function": {"name": name}}
        for name in (
            "get_salon_information",
            "check_availability",
            "book_appointment",
            "find_appointments",
            "reschedule_appointment",
            "cancel_appointment",
            "take_message",
        )
    ]
    selected = select_voice_tool_schemas(
        tools,
        intent="unknown",
        routing_reason="consequential_action_request",
        latest_user_text=(
            "I have a booking for Friday at 3PM under Shell. "
            "I'd like to reschedule it to another time, please."
        ),
    )
    names = {item["function"]["name"] for item in selected}
    assert names == {
        "get_salon_information",
        "find_appointments",
        "check_availability",
        "reschedule_appointment",
        "take_message",
    }
    assert "book_appointment" not in names
    assert "cancel_appointment" not in names


def test_unknown_intent_keeps_all_tools_and_unrecognised_schema_entries():
    tools = [
        {"type": "function", "function": {"name": "book_appointment"}},
        {"type": "function", "function": {"name": "future_tool"}},
        {"type": "custom", "custom": {"name": "provider_extension"}},
    ]
    assert select_voice_tool_schemas(tools, intent="unknown") == tools
    assert select_voice_tool_schemas(tools, intent="booking") == tools


def test_booking_tool_scoping_reduces_admitted_request_estimate():
    budget = RollingModelTokenBudget(tokens_per_minute=7000)
    tools = [
        {
            "type": "function",
            "function": {"name": name, "description": "workflow details " * 80},
        }
        for name in (
            "get_salon_information",
            "check_availability",
            "book_appointment",
            "find_appointments",
            "reschedule_appointment",
            "cancel_appointment",
            "take_message",
        )
    ]
    params = {
        "messages": [{"role": "user", "content": "I want to book a haircut"}],
        "tools": tools,
        "max_completion_tokens": 256,
    }
    original_estimate = budget.estimate_request_tokens(params)
    params["tools"] = select_voice_tool_schemas(tools, intent="booking")
    scoped_estimate = budget.estimate_request_tokens(params)
    assert scoped_estimate < original_estimate
    assert len(params["tools"]) == 4
