from pathlib import Path

from services.voice.runtime_control import is_generic_booking_request


def test_exact_live_failure_phrase_is_deterministic_booking_intent():
    assert is_generic_booking_request("I book in an appointment?") is True


def test_runtime_uses_shared_booking_intent_detector_before_llm():
    source = (Path(__file__).parents[1] / "apps" / "voice" / "bot.py").read_text()
    assert "generic_booking_request = is_generic_booking_request(latest_user)" in source
    assert "and is_generic_booking_request(message.content):" in source
    assert "PRISM LINK deterministic generic booking intent fastpath" in source
