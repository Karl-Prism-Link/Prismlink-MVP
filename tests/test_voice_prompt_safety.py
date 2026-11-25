from types import SimpleNamespace

from apps.voice.prompt import (
    build_voice_reschedule_system_prompt,
    build_voice_system_prompt,
)


def test_system_prompt_forbids_medical_advice_and_repetitive_out_of_scope_refusals():
    prompt = build_voice_system_prompt(
        SimpleNamespace(name="Luxe Hair & Beauty", timezone="Pacific/Auckland")
    )
    assert "Never provide medical advice" in prompt
    assert "Never invent an emergency team" in prompt
    assert "do not repeat the same refusal verbatim" in prompt
    assert "Let the caller finish their thought" in prompt
    assert "ACTIVE WORKFLOW AND ACTION PRESERVATION" in prompt
    assert "An unclear follow-up must never change the active workflow" in prompt
    assert "Do not invoke a message-taking action while a booking" in prompt
    assert "make the spoken response and call outcome match that exact tool result" in prompt


def test_system_prompt_recovers_naturally_from_incomplete_speech():
    prompt = build_voice_system_prompt(
        SimpleNamespace(name="Luxe Hair & Beauty", timezone="Pacific/Auckland")
    )
    assert "INCOMPLETE SPEECH AND CONVERSATION RECOVERY" in prompt
    assert (
        "Never tell the caller there is a connection issue merely because their sentence is incomplete"
        in prompt
    )
    assert '"Of course. What would you like to do?"' in prompt
    assert "Do not repeat the same recovery sentence on consecutive turns" in prompt
    assert (
        "Only describe a connection or service problem when the runtime explicitly reports a technical failure"
        in prompt
    )


def test_reschedule_prompt_is_compact_and_preserves_safety_and_confirmation():
    prompt = build_voice_reschedule_system_prompt(
        SimpleNamespace(name="Luxe Hair & Beauty", timezone="Pacific/Auckland")
    )
    assert len(prompt) < 5000
    assert "Caller ID is not verification" in prompt
    assert "Use find_appointments" in prompt
    assert "use check_availability" in prompt
    assert "fresh, explicit yes" in prompt
    assert "Never disclose another customer's appointment" in prompt
    assert "do not claim success" in prompt
