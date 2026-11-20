from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from database.repositories import AppointmentRepository, TenantRepository
from database.session import DBSession
from services.voice.session import LiveCallSession
from services.voice.tools import VoiceToolset


def register(client, *, name: str, slug: str, email: str) -> str:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "salon_name": name,
            "slug": slug,
            "email": email,
            "password": "StrongPass123!",
            "timezone": "Pacific/Auckland",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def next_weekday_iso(weekday: int, hour: int = 14) -> str:
    now = datetime.now(ZoneInfo("Pacific/Auckland"))
    days = (weekday - now.weekday()) % 7
    if days == 0:
        days = 7
    target = (now + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return target.isoformat()


async def booking_context(client, *, slug: str):
    token = register(
        client,
        name=f"{slug} Salon",
        slug=slug,
        email=f"{slug}@example.com",
    )
    service_response = client.post(
        "/api/v1/salon/services",
        headers=auth(token),
        json={"name": "Haircut", "duration_minutes": 45},
    )
    assert service_response.status_code == 201, service_response.text

    hours = [
        {
            "day_of_week": day,
            "opens_at": "09:00",
            "closes_at": "17:00",
            "is_closed": False,
        }
        for day in range(5)
    ] + [
        {"day_of_week": 5, "opens_at": None, "closes_at": None, "is_closed": True},
        {"day_of_week": 6, "opens_at": None, "closes_at": None, "is_closed": True},
    ]
    hours_response = client.put("/api/v1/salon/hours", headers=auth(token), json=hours)
    assert hours_response.status_code == 200, hours_response.text

    db = DBSession()
    tenant = await TenantRepository(db).by_slug(slug)
    assert tenant is not None
    live = LiveCallSession(
        db,
        tenant_id=tenant.id,
        external_call_id=f"{slug}-voice-regression",
        caller_number="0213333333",
        called_number=tenant.phone_number,
    )
    await live.start()
    await live.append_transcript(
        "caller",
        "My name is Jane Doe and my number is zero two one three three three three three three.",
    )
    return db, live, VoiceToolset(db, tenant, live)


@pytest.mark.asyncio
async def test_booking_time_change_replaces_prepared_slot_and_requires_fresh_yes(client):
    db, live, tools = await booking_context(client, slug="booking-time-change")
    try:
        tuesday = next_weekday_iso(1)
        thursday = next_weekday_iso(3)
        tuesday_phrase = "next Tuesday at 2 pm"
        thursday_phrase = "next Thursday at 2 pm"

        first = await tools.book_appointment(
            "Haircut",
            tuesday,
            "Jane Doe",
            "0213333333",
            False,
            caller_time_phrase=tuesday_phrase,
        )
        assert first["ok"] is True
        assert first["ready_to_confirm"] is True
        assert "Tuesday" in first["spoken_start"]

        # A yes to the first readback must not authorize the caller's changed request.
        await live.append_transcript("assistant", f"{first['spoken_start']}. Is that correct?")
        await live.append_transcript("caller", "Yes.")
        await live.append_transcript("caller", f"Actually, {thursday_phrase}.")

        changed = await tools.book_appointment(
            "Haircut",
            thursday,
            "Jane Doe",
            "0213333333",
            False,
            caller_time_phrase=thursday_phrase,
        )
        assert changed["ok"] is True
        assert changed["ready_to_confirm"] is True
        assert "Thursday" in changed["spoken_start"]
        assert changed["starts_at"] != first["starts_at"]

        # The caller has not yet confirmed the new Thursday readback.
        premature = await tools.book_appointment(
            "Haircut",
            thursday,
            "Jane Doe",
            "0213333333",
            True,
            caller_time_phrase=thursday_phrase,
        )
        assert premature["ok"] is False
        assert premature["booked"] is False
        assert premature["code"] == "CONFIRMATION_REQUIRED"
        assert await AppointmentRepository(db, live.tenant_id).list() == []

        await live.append_transcript(
            "assistant",
            f"{changed['spoken_start']}, Jane Doe, {changed['spoken_phone']}. Is that correct?",
        )
        await live.append_transcript("caller", "Yes, that's correct.")
        created = await tools.book_appointment(
            "Haircut",
            thursday,
            "Jane Doe",
            "0213333333",
            True,
            caller_time_phrase="Thursday at 2 pm",
        )
        assert created["ok"] is True
        assert created["booked"] is True
        assert "Thursday" in created["spoken_start"]

        appointments = await AppointmentRepository(db, live.tenant_id).list()
        assert len(appointments) == 1
        assert appointments[0].starts_at.astimezone(ZoneInfo("Pacific/Auckland")).weekday() == 3
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_booking_tool_failure_never_reports_success_or_creates_appointment(
    client, monkeypatch
):
    db, live, tools = await booking_context(client, slug="booking-tool-failure")
    try:
        starts_at = next_weekday_iso(1)
        prepared = await tools.book_appointment(
            "Haircut",
            starts_at,
            "Jane Doe",
            "0213333333",
            False,
            caller_time_phrase="next Tuesday at 2 pm",
        )
        assert prepared["ok"] is True
        assert prepared["booked"] is False

        await live.append_transcript(
            "assistant",
            f"{prepared['spoken_start']}, Jane Doe, {prepared['spoken_phone']}. Is that correct?",
        )
        await live.append_transcript("caller", "Yes, that's correct.")

        async def fail_create(*args, **kwargs):
            raise HTTPException(status_code=503, detail="Calendar service unavailable")

        monkeypatch.setattr(tools.booking, "create", fail_create)
        result = await tools.book_appointment(
            "Haircut",
            starts_at,
            "Jane Doe",
            "0213333333",
            True,
            caller_time_phrase=datetime.fromisoformat(starts_at).strftime("%A %d %B %Y at 2 pm"),
        )

        assert result["ok"] is False
        assert result.get("booked") is not True
        assert result["code"] == "HTTP_503"
        assert "Calendar service unavailable" in result["message"]
        assert await AppointmentRepository(db, live.tenant_id).list() == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_booking_confirmation_without_fresh_explicit_yes_is_rejected(client):
    db, live, tools = await booking_context(client, slug="booking-confirmation-gate")
    try:
        starts_at = next_weekday_iso(1)
        prepared = await tools.book_appointment(
            "Haircut",
            starts_at,
            "Jane Doe",
            "0213333333",
            False,
            caller_time_phrase=datetime.fromisoformat(starts_at).strftime("%A %d %B %Y at 2 pm"),
        )
        assert prepared["ready_to_confirm"] is True

        await live.append_transcript(
            "assistant",
            f"{prepared['spoken_start']}, Jane Doe, {prepared['spoken_phone']}. Is that correct?",
        )
        await live.append_transcript("caller", "What time was that again?")
        result = await tools.book_appointment(
            "Haircut",
            starts_at,
            "Jane Doe",
            "0213333333",
            True,
            caller_time_phrase=datetime.fromisoformat(starts_at).strftime("%A %d %B %Y at 2 pm"),
        )

        assert result["ok"] is False
        assert result["booked"] is False
        assert result["code"] == "CONFIRMATION_REQUIRED"
        assert await AppointmentRepository(db, live.tenant_id).list() == []
    finally:
        await db.close()

@pytest.mark.asyncio
async def test_same_day_weekday_bare_time_rolls_to_next_week_when_opening_time_has_passed(
    client, monkeypatch
):
    from datetime import datetime as RealDateTime

    db, live, tools = await booking_context(client, slug="booking-same-day-past-bare-time")
    try:
        class FrozenDateTime(RealDateTime):
            @classmethod
            def now(cls, tz=None):
                fixed = RealDateTime(
                    2026, 11, 20, 15, 37, tzinfo=ZoneInfo("Pacific/Auckland")
                )
                return fixed if tz is None else fixed.astimezone(tz)

        monkeypatch.setattr("services.voice.tools.datetime", FrozenDateTime)

        async def free_calendar(*, starts_at, service_id, staff_member_id=None):
            return True, starts_at + timedelta(minutes=45)

        monkeypatch.setattr(tools.booking, "availability", free_calendar)
        result = await tools.check_availability(
            "Haircut", "", caller_time_phrase="Friday 10:00"
        )

        assert result["ok"] is True
        assert result["available"] is True
        assert result["local_date"] == "2026-11-27"
        assert result["local_time"] == "10:00"
        assert result["meridiem_inferred_from_business_hours"] is True
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_find_available_times_returns_only_calendar_confirmed_slots(client, monkeypatch):
    from datetime import datetime as RealDateTime

    db, live, tools = await booking_context(client, slug="booking-available-times")
    try:
        class FrozenDateTime(RealDateTime):
            @classmethod
            def now(cls, tz=None):
                fixed = RealDateTime(2026, 11, 20, 15, 37, tzinfo=ZoneInfo("Pacific/Auckland"))
                return fixed if tz is None else fixed.astimezone(tz)

        monkeypatch.setattr("services.voice.tools.datetime", FrozenDateTime)
        checked = []

        async def calendar_availability(*, starts_at, service_id, staff_member_id=None):
            checked.append(starts_at.astimezone(ZoneInfo("Pacific/Auckland")).strftime("%H:%M"))
            # The 09:00 slot is busy; later slots are free.
            return checked[-1] != "09:00", starts_at + timedelta(minutes=45)

        monkeypatch.setattr(tools.booking, "availability", calendar_availability)
        result = await tools.find_available_times("Haircut", "next Friday")

        assert result["ok"] is True
        assert result["local_date"] == "2026-11-27"
        assert [slot["spoken_start"].split(" at ")[-1] for slot in result["slots"]] == [
            "9:30 AM", "10:00 AM", "10:30 AM"
        ]
        assert checked[:4] == ["09:00", "09:30", "10:00", "10:30"]

        same_day = await tools.find_available_times("Haircut", "Friday 10:00")
        assert same_day["local_date"] == "2026-11-27"
    finally:
        await db.close()
