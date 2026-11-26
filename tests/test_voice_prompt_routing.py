from types import SimpleNamespace

from apps.voice.prompt import build_voice_system_prompt_for_turn
from services.voice.model_router import route_voice_turn


def test_first_unknown_turn_routed_as_enquiry_selects_compact_prompt():
    decision = route_voice_turn(
        intent="unknown",
        service=None,
        starts_at=None,
        last_tool=None,
        last_tool_ok=None,
        confirmed=False,
        latest_user_text="What services do you offer?",
    )
    assert decision.reason == "salon_enquiry"

    tenant = SimpleNamespace(name="Test Salon")
    prompt = build_voice_system_prompt_for_turn(
        tenant,
        intent="unknown",
        default_prompt="FULL DEFAULT RECEPTIONIST PROMPT",
        routing_reason=decision.reason,
    )

    assert "ENQUIRIES AND CONTEXT" in prompt
    assert "FULL DEFAULT RECEPTIONIST PROMPT" not in prompt


def test_booking_prompt_is_not_replaced_by_enquiry_routing_reason():
    tenant = SimpleNamespace(name="Test Salon")
    prompt = build_voice_system_prompt_for_turn(
        tenant,
        intent="booking",
        default_prompt="FULL DEFAULT RECEPTIONIST PROMPT",
        routing_reason="booking_logic",
    )

    assert prompt == "FULL DEFAULT RECEPTIONIST PROMPT"
