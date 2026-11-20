from types import SimpleNamespace

from apps.voice.prompt import build_voice_greeting, build_voice_system_prompt


def _tenant(**overrides):
    values = {
        "name": "Luxe Hair & Beauty",
        "timezone": "Pacific/Auckland",
        "phone_number": "09 2431316",
        "address": "123 Queen Street, Auckland 1010",
        "contact_email": "Prismlink26@gmail.com",
        "currency": "NZD",
        "prices_include_gst": True,
        "ai_disclosure": "You are speaking with the salon’s AI receptionist.",
        "walk_ins_policy": "Accepted when capacity permits; appointments are recommended.",
        "parking_info": "Paid street parking and a public car park nearby.",
        "accessibility_info": "Step-free front entrance. Ask staff about treatment-room access.",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_voice_prompt_includes_configured_tenant_profile_facts():
    prompt = build_voice_system_prompt(_tenant())

    for fact in (
        "123 Queen Street, Auckland 1010",
        "Paid street parking and a public car park nearby.",
        "Step-free front entrance. Ask staff about treatment-room access.",
        "Accepted when capacity permits; appointments are recommended.",
        "You are speaking with the salon’s AI receptionist.",
        "Currency: NZD",
        "Prices include GST: yes",
    ):
        assert fact in prompt
    assert "Give the configured address when asked where the salon is." in prompt
    assert "Never calculate or invent a price." in prompt


def test_voice_prompt_does_not_invent_missing_profile_details():
    prompt = build_voice_system_prompt(
        _tenant(address=None, parking_info="", accessibility_info=None)
    )

    assert "Address:" not in prompt
    assert "Parking information:" not in prompt
    assert "Accessibility information:" not in prompt
    assert "If a field is absent or blank, say you do not have that detail confirmed" in prompt


def test_live_greeting_uses_configured_ai_disclosure():
    tenant = _tenant()

    assert build_voice_greeting(tenant) == (
        "Thanks for calling Luxe Hair & Beauty! "
        "You are speaking with the salon’s AI receptionist. What can I do for you today?"
    )


def test_live_greeting_has_safe_ai_disclosure_fallback():
    tenant = _tenant(ai_disclosure=None)

    assert "I'm the salon's AI receptionist." in build_voice_greeting(tenant)
