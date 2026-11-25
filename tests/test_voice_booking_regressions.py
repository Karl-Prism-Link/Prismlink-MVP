from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from database.repositories import AppointmentRepository, CallRepository, TenantRepository
from database.session import DBSession
from services.voice.session import LiveCallSession
from services.voice.speech_cleanup import booking_success_speech
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
async def test_immediate_safety_state_survives_session_rehydration(client):
    db, live, _tools = await booking_context(client, slug="safety-state-rehydration")
    restored_db = DBSession()
    try:
        await live.activate_immediate_safety("immediate_emergency")

        restored = LiveCallSession(
            restored_db,
            tenant_id=live.tenant_id,
            external_call_id=live.external_call_id,
            caller_number=live.caller_number,
            called_number=live.called_number,
        )
        await restored.start()

        assert restored.call is not None
        assert restored.call.id == live.call.id
        assert restored.immediate_safety_active is True
        assert restored.voice_state.safety_reason == "immediate_emergency"
        assert restored.voice_state.safety_response_sent is False

        await restored.append_transcript("caller", "The emergency remains active")
        assert restored._sequence == 2
        assert restored.latest_caller_transcript == "The emergency remains active"

        await restored.mark_safety_response_sent()
        reloaded_db = DBSession()
        try:
            reloaded = LiveCallSession(
                reloaded_db,
                tenant_id=live.tenant_id,
                external_call_id=live.external_call_id,
                caller_number=live.caller_number,
                called_number=live.called_number,
            )
            await reloaded.start()
            assert reloaded.immediate_safety_active is True
            assert reloaded.voice_state.safety_reason == "immediate_emergency"
            assert reloaded.voice_state.safety_response_sent is True
        finally:
            await reloaded_db.close()
    finally:
        await restored_db.close()
        await db.close()


@pytest.mark.asyncio
async def test_new_call_starts_with_fresh_inactive_safety_state(client):
    db, live, _tools = await booking_context(client, slug="safety-fresh-call")
    fresh_db = DBSession()
    try:
        await live.activate_immediate_safety("immediate_emergency")
        assert live.immediate_safety_active is True

        fresh = LiveCallSession(
            fresh_db,
            tenant_id=live.tenant_id,
            external_call_id=f"{live.external_call_id}-next",
            caller_number=live.caller_number,
            called_number=live.called_number,
        )
        await fresh.start()

        assert fresh.call is not None
        assert fresh.call.id != live.call.id
        assert fresh.immediate_safety_active is False
        assert fresh.voice_state.safety_reason is None
        assert fresh.voice_state.safety_response_sent is False
    finally:
        await fresh_db.close()
        await db.close()


@pytest.mark.asyncio
async def test_mock_booking_lifecycle_persists_consistent_call_evidence(client):
    db, live, tools = await booking_context(client, slug="booking-lifecycle-evidence")
    try:
        starts_at = next_weekday_iso(2)
        time_phrase = datetime.fromisoformat(starts_at).strftime("%A %d %B %Y at 2 pm")
        await live.append_transcript(
            "caller",
            "My name is Jane Doe and my number is zero two one three three three three three three three.",
        )

        availability = await tools.check_availability(
            "Haircut", starts_at, caller_time_phrase=time_phrase
        )
        assert availability["ok"] is True
        assert availability["available"] is True
        live.update_voice_state(
            intent="booking",
            service=availability["service"],
            starts_at=availability["starts_at"],
            caller_time_phrase=availability["caller_time_phrase"],
            last_tool="check_availability",
            last_tool_ok=True,
        )

        prepared = await tools.book_appointment(
            "Haircut",
            starts_at,
            "Jane Doe",
            "0213333333",
            False,
            caller_time_phrase=time_phrase,
        )
        assert prepared["ok"] is True
        assert prepared["ready_to_confirm"] is True
        assert prepared["booked"] is False
        appointments_before_confirmation = await AppointmentRepository(db, live.tenant_id).list()
        assert not [
            appointment
            for appointment in appointments_before_confirmation
            if appointment.call_id == live.call_id
        ]
        confirmation = (
            f"{prepared['spoken_start']}, Jane Doe, {prepared['spoken_phone']}. Is that correct?"
        )
        await live.append_transcript("assistant", confirmation)
        await live.append_transcript("caller", "Yes, that's correct.")

        created = await tools.book_appointment(
            "Haircut",
            starts_at,
            "Jane Doe",
            "0213333333",
            True,
            caller_time_phrase=time_phrase,
        )
        assert created["ok"] is True
        assert created["booked"] is True
        spoken_confirmation = booking_success_speech(created)
        await live.append_transcript("assistant", spoken_confirmation)

        live.update_voice_state(
            intent="booking",
            service=created["service"],
            staff=created.get("staff"),
            starts_at=created["starts_at"],
            customer_name=created["customer_name"],
            customer_phone=created["customer_phone"],
            confirmed=True,
            last_tool="book_appointment",
            last_tool_ok=True,
        )
        live.set_resolution(
            "booked", f"Booked {created['customer_name']} for {created['starts_at']}."
        )
        await live.finish()

        appointments = await AppointmentRepository(db, live.tenant_id).list()
        assert len(appointments) == 1
        appointment = appointments[0]
        assert appointment.call_id == live.call_id
        assert appointment.customer_name == "Jane Doe"
        assert appointment.customer_phone == "0213333333"
        assert appointment.starts_at.replace(tzinfo=UTC) == datetime.fromisoformat(
            created["starts_at"]
        ).astimezone(UTC)
        assert appointment.status == "booked"

        call = await CallRepository(db, live.tenant_id).get(live.call_id)
        summary = await CallRepository(db, live.tenant_id).summary(live.call_id)
        assert call is not None
        assert call.status == "completed"
        assert call.outcome == "booked"
        assert summary is not None
        assert summary.outcome == call.outcome
        assert "Jane Doe" in summary.summary_text

        from sqlalchemy import select

        from database.models import TranscriptMessage

        transcript_result = await db.execute(
            select(TranscriptMessage)
            .where(TranscriptMessage.call_id == live.call_id)
            .order_by(TranscriptMessage.sequence_number)
        )
        transcript = list(transcript_result.scalars())
        assert [row.sequence_number for row in transcript] == list(range(1, len(transcript) + 1))
        assert transcript[-3].speaker == "assistant"
        assert transcript[-3].text == confirmation
        assert transcript[-2].speaker == "caller"
        assert transcript[-2].text == "Yes, that's correct."
        assert transcript[-1].speaker == "assistant"
        assert transcript[-1].text == spoken_confirmation
        assert "booked" in transcript[-1].text.casefold()
    finally:
        await db.close()


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
                fixed = RealDateTime(2026, 11, 20, 15, 37, tzinfo=ZoneInfo("Pacific/Auckland"))
                return fixed if tz is None else fixed.astimezone(tz)

        monkeypatch.setattr("services.voice.tools.datetime", FrozenDateTime)

        async def free_calendar(*, starts_at, service_id, staff_member_id=None):
            return True, starts_at + timedelta(minutes=45)

        monkeypatch.setattr(tools.booking, "availability", free_calendar)
        result = await tools.check_availability("Haircut", "", caller_time_phrase="Friday 10:00")

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
            "9:30 AM",
            "10:00 AM",
            "10:30 AM",
        ]
        assert checked[:4] == ["09:00", "09:30", "10:00", "10:30"]

        same_day = await tools.find_available_times("Haircut", "Friday 10:00")
        assert same_day["local_date"] == "2026-11-27"
    finally:
        await db.close()
