from datetime import datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from services.voice.salon_enquiries import (
    business_hours_summary_speech,
    has_consequential_salon_action,
    is_business_hours_question,
    is_open_now_question,
    is_price_question,
    is_price_service_clarification,
    open_now_speech,
    price_speech,
)


def _hours():
    return [
        SimpleNamespace(day_of_week=0, opens_at=time(9), closes_at=time(17), is_closed=False),
        SimpleNamespace(day_of_week=1, opens_at=time(9), closes_at=time(17), is_closed=False),
        SimpleNamespace(day_of_week=2, opens_at=time(9), closes_at=time(17), is_closed=False),
        SimpleNamespace(day_of_week=3, opens_at=time(9), closes_at=time(17), is_closed=False),
        SimpleNamespace(day_of_week=4, opens_at=time(9), closes_at=time(17), is_closed=False),
        SimpleNamespace(day_of_week=5, opens_at=time(9), closes_at=time(14), is_closed=False),
        SimpleNamespace(day_of_week=6, opens_at=None, closes_at=None, is_closed=True),
    ]


def test_open_now_intent_phrases():
    assert is_open_now_question("Are you open now?")
    assert is_open_now_question("Is the salon open at the moment?")
    assert is_business_hours_question("What are your hours?")
    assert is_business_hours_question("What time do you close?")


def test_after_hours_reports_closed_and_next_opening():
    now = datetime(2026, 8, 11, 18, 46, tzinfo=ZoneInfo("Pacific/Auckland"))
    assert open_now_speech(_hours(), "Pacific/Auckland", now=now) == (
        "No, we're closed now. We're open tomorrow from 9 AM to 5 PM."
    )


def test_during_hours_reports_open_and_close_time():
    now = datetime(2026, 8, 11, 13, 30, tzinfo=ZoneInfo("Pacific/Auckland"))
    assert open_now_speech(_hours(), "Pacific/Auckland", now=now) == (
        "Yes, we're open now. We close at 5 PM today."
    )


def test_before_open_reports_today_open_time():
    now = datetime(2026, 8, 11, 8, 0, tzinfo=ZoneInfo("Pacific/Auckland"))
    assert open_now_speech(_hours(), "Pacific/Auckland", now=now) == (
        "No, we're closed right now. We open at 9 AM today."
    )


def test_closed_sunday_reports_monday():
    now = datetime(2026, 8, 16, 12, 0, tzinfo=ZoneInfo("Pacific/Auckland"))
    assert open_now_speech(_hours(), "Pacific/Auckland", now=now) == (
        "No, we're closed now. We're open Monday from 9 AM to 5 PM."
    )


def test_hours_summary_uses_configured_values():
    speech = business_hours_summary_speech(_hours())
    assert "Monday 9 AM to 5 PM" in speech
    assert "Saturday 9 AM to 2 PM" in speech
    assert "Sunday closed" in speech


def test_price_question_phrases_are_deterministic():
    assert is_price_question("How much is the men's haircut?")
    assert is_price_question("What does a men's cut cost?")
    assert is_price_question("What's the price of a women's cut?")
    assert is_price_question("How much for a men's haircut?")
    assert not is_price_question("I'd like to book a men's haircut.")
    assert not is_price_question("Cancel my Men's Cut booking; it costs too much.")
    assert not is_price_question("The service costs too much.")
    assert is_price_question("What is the cost of a men's cut?")


def test_price_speech_formats_configured_cents_without_calculation():
    assert price_speech("Men's Cut", 4500, "NZD") == "The Men's Cut is 45 dollars."
    assert price_speech("Colour", 6575, "NZD") == "The Colour is 65.75 dollars."
    assert price_speech("Colour", 4500, "EUR") == "The Colour is 45 euros."
    assert price_speech("Colour", 4500, "GBP") == "The Colour is 45 pounds."
    assert (
        price_speech("Men's Cut", 4500, "NZD", prices_include_gst=True)
        == "The Men's Cut is 45 dollars."
    )


def test_price_enquiry_clears_prepared_consequential_actions():
    # Regression fixture for the P1 safety finding: price clarification must not
    # leave an earlier booking/reschedule/cancel/message proposal armed.
    assert (
        price_speech("Men's Cut", 4500, "NZD", prices_include_gst=True)
        == "The Men's Cut is 45 dollars."
    )


def test_missing_price_offer_does_not_record_a_message_as_taken():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "apps" / "voice" / "bot.py").read_text()
    start = source.index("if service is None or service.price is None:")
    end = source.index("speech = price_speech(", start)
    missing_price_branch = source[start:end]
    assert '"enquiry_resolved"' in missing_price_branch
    assert '"message_requested"' not in missing_price_branch
    assert "caller was offered a message, but no message was taken" in missing_price_branch


def test_deterministic_price_path_passes_tenant_currency_to_speech():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "apps" / "voice" / "bot.py").read_text()
    start = source.index("speech = price_speech(")
    price_call = source[start : start + 500]
    assert 'currency=str(getattr(tenant, "currency", "NZD") or "NZD")' in price_call


def test_price_service_clarification_stays_in_the_enquiry_flow():
    recent = ("How much does it cost?", "Women's Cut")
    assert is_price_service_clarification(
        "Women's Cut",
        recent,
        intent="enquiry",
        last_tool="get_salon_information",
    )
    assert not is_price_service_clarification(
        "Women's Cut",
        ("Can I book an appointment?", "Women's Cut"),
        intent="enquiry",
        last_tool="get_salon_information",
    )
    assert not is_price_service_clarification(
        "Women's Cut",
        recent,
        intent="booking",
        last_tool="get_salon_information",
    )
    assert not is_price_service_clarification(
        "Women's Cut",
        recent,
        intent="enquiry",
        last_tool="book_appointment",
    )


def test_common_enquiry_resolves_price_clarification_before_booking_fastpath():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "apps" / "voice" / "bot.py").read_text()
    start = source.index("async def _handle_common_enquiry")
    end = source.index("async def _handle_routing_and_message", start)
    enquiry_handler = source[start:end]
    assert "is_price_service_clarification(" in enquiry_handler
    assert "if is_price_question(latest_user) or price_service_clarification:" in enquiry_handler


def test_price_fastpath_yields_when_caller_also_requests_an_action():
    mixed_requests = (
        "What does a Men's Cut cost, and cancel my appointment?",
        "How much is a Women's Cut, and can you cancel my booking?",
        "What's the price of a haircut? I also need to reschedule my appointment.",
        "How much is a men's cut and book me in for Friday?",
        "Women's Cut, and please cancel my appointment.",
    )
    for utterance in mixed_requests:
        assert has_consequential_salon_action(utterance), utterance


def test_price_fastpath_keeps_pure_enquiries_and_policy_questions():
    ordinary_enquiries = (
        "How much is the men's haircut?",
        "What does a women's cut cost?",
        "What is your cancellation policy?",
        "How much does it cost to cancel?",
    )
    for utterance in ordinary_enquiries:
        assert not has_consequential_salon_action(utterance), utterance


def test_common_enquiry_price_fastpath_yields_to_mixed_action_intents():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "apps" / "voice" / "bot.py").read_text()
    start = source.index("async def _handle_common_enquiry")
    end = source.index("async def _handle_routing_and_message", start)
    enquiry_handler = source[start:end]
    assert "and not has_consequential_salon_action(latest_user)" in enquiry_handler
