from datetime import datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from services.voice.salon_enquiries import (
    business_hours_summary_speech,
    is_business_hours_question,
    is_open_now_question,
    open_now_speech,
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
