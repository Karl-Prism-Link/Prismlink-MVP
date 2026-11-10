from types import SimpleNamespace

from apps.voice.prompt import build_voice_system_prompt


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
