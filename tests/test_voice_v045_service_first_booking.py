from pathlib import Path

from services.service_names import configured_service_name_from_speech
from services.voice.contact import extract_booking_day_reference, recent_booking_datetime_phrase


def test_bare_service_reply_is_a_configured_service_candidate():
    configured = ("Women's Cut", "Men's Cut", "Blow Wave", "Colour")
    assert configured_service_name_from_speech("Means Cut.", configured) == "Men's Cut"


def test_relative_day_is_preserved_without_calculating_date():
    assert extract_booking_day_reference("Tomorrow.") == "tomorrow"
    assert recent_booking_datetime_phrase(("Means Cut.", "Tomorrow.")) is None


def test_service_first_runtime_treats_service_as_booking_context():
    source = (Path(__file__).parents[1] / "apps" / "voice" / "bot.py").read_text()
    assert "or service_reply is not None" in source
    assert "or bool(state.service)" in source
    assert "What time on {day} would you like?" in source


def test_rate_limit_recovery_uses_booking_state_instead_of_repeat_loop():
    source = (Path(__file__).parents[1] / "apps" / "voice" / "bot.py").read_text()
    assert "def _rate_limit_recovery_speech" in source
    assert 'return "What day and time would you like?"' in source
    assert 'return f"What time on {day} would you like?"' in source
    assert (
        "Could you repeat that?"
        not in source[
            source.index("def _rate_limit_recovery_speech") : source.index(
                "async def finish_and_cancel"
            )
        ]
    )
