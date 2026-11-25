from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from pipecat.frames.frames import LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from sqlalchemy import select

from database.models import Appointment
from database.repositories import TenantRepository
from database.session import DBSession
from services.voice.confirmation import handle_confirmation_turn
from services.voice.session import LiveCallSession
from services.voice.tool_handlers import create_voice_tool_handlers
from services.voice.tools import VoiceToolset
from services.voice.turn_handlers import latest_caller_text_from_frame
from tests.test_mvp import add_service, auth, register


class HandlerParams:
    def __init__(self, arguments: dict):
        self.arguments = arguments
        self.results = []

    async def result_callback(self, result, properties=None):
        self.results.append((result, properties))


async def _speak(_text, _direction):
    return None


def _create_appointment(client, token, service_id: str, starts_at: datetime, phone: str):
    response = client.post(
        "/api/v1/appointments",
        headers=auth(token),
        json={
            "customer_name": "Jamie Example",
            "customer_phone": phone,
            "service_id": service_id,
            "starts_at": starts_at.astimezone(UTC).isoformat(),
            "idempotency_key": f"frame-db-{uuid4().hex}",
            "confirmed": True,
            "source": "voice",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("workflow", "caller_reply", "expected_status", "expected_handled", "expected_prepared"),
    [
        ("cancel", "yes", "cancelled", True, False),
        ("cancel", "no", "booked", True, False),
        ("cancel", "yes, but change the time", "booked", False, True),
        ("reschedule", "yes", "rescheduled", True, False),
        ("reschedule", "no", "booked", True, False),
        ("reschedule", "yes, but change the time", "booked", False, True),
    ],
)
async def test_real_pipecat_frame_confirmation_updates_isolated_appointment_database(
    client, workflow, caller_reply, expected_status, expected_handled, expected_prepared
):
    token = register(
        client,
        name=f"Frame Integration {workflow.title()} {caller_reply[:2]}",
        slug=f"frame-integration-{workflow}-{uuid4().hex[:8]}",
        email=f"frame-{workflow}-{uuid4().hex[:8]}@example.com",
    )
    service = add_service(client, token, "Long Cut", 45)
    tz = ZoneInfo("Pacific/Auckland")
    old_start = (datetime.now(tz) + timedelta(days=6)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    new_start = (datetime.now(tz) + timedelta(days=8)).replace(
        hour=11, minute=0, second=0, microsecond=0
    )
    created = _create_appointment(client, token, service["id"], old_start, "0215551234")

    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug(
            # The token is scoped to the unique tenant created above; resolve via API profile.
            client.get("/api/v1/salon", headers=auth(token)).json()["slug"]
        )
        assert tenant is not None
        session = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id=f"frame-db-{uuid4().hex}",
            caller_number="0215551234",
            called_number=tenant.phone_number,
        )
        await session.start()
        tools = VoiceToolset(db, tenant, session)
        handlers = create_voice_tool_handlers(session, tools)
        session.update_voice_state(intent=workflow)
        await session.append_transcript("caller", "My booking phone is 0215551234")

        lookup = HandlerParams({"customer_phone": "0215551234"})
        await handlers["find_appointments"](lookup)
        assert lookup.results
        assert lookup.results[-1][0]["ok"] is True
        assert session.voice_state.appointment_id == created["id"]

        if workflow == "cancel":
            assert session.has_prepared_cancel is True
        else:
            availability = HandlerParams(
                {
                    "service_name": "LLM supplied wrong service",
                    "starts_at_local": new_start.isoformat(),
                    "staff_name": None,
                    "caller_time_phrase": f"{new_start.strftime('%A')} {new_start.day} {new_start.strftime('%B')} at 11 AM",
                }
            )
            await handlers["check_availability"](availability)
            assert availability.results[-1][0]["ok"] is True
            assert availability.results[-1][0]["available"] is True
            assert availability.results[-1][0]["service"] == "Long Cut"
            assert datetime.fromisoformat(availability.results[-1][0]["starts_at"]) == new_start
            assert session.has_prepared_reschedule is True

        # Persist the actual caller turn, then pass a real Pipecat context frame through
        # the same transcript extraction and confirmation dispatcher used by the bot.
        await session.append_transcript("assistant", "Do you confirm this change?")
        await session.append_transcript("caller", caller_reply)
        frame = LLMContextFrame(LLMContext(messages=[{"role": "user", "content": caller_reply}]))
        latest = await latest_caller_text_from_frame(frame, session)
        assert latest == caller_reply
        handled = await handle_confirmation_turn(
            latest,
            call_session=session,
            tools=tools,
            speak=_speak,
            direction="downstream",
        )
        assert handled is expected_handled
        assert (
            session.has_prepared_cancel if workflow == "cancel" else session.has_prepared_reschedule
        ) is expected_prepared

        persisted_result = await db.execute(
            select(Appointment).where(Appointment.id == UUID(created["id"]))
        )
        persisted = persisted_result.scalar_one_or_none()
        assert persisted is not None
        assert persisted.status == expected_status
        if workflow == "reschedule" and caller_reply == "yes":
            assert datetime.fromisoformat(session.voice_state.starts_at) == new_start
        if workflow == "reschedule" and expected_status == "rescheduled":
            assert persisted.starts_at.replace(tzinfo=UTC) == new_start.astimezone(UTC)
        else:
            assert persisted.starts_at.replace(tzinfo=UTC) == old_start.astimezone(UTC)
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("caller_reply", "expected_bookings", "expected_handled", "expected_prepared"),
    [
        ("yes", 1, True, False),
        ("no", 0, True, False),
        ("yes, but change the time", 0, False, True),
    ],
)
async def test_real_pipecat_frame_booking_confirmation_persists_only_exact_yes(
    client, caller_reply, expected_bookings, expected_handled, expected_prepared
):
    token = register(
        client,
        name="Frame Booking Integration",
        slug=f"frame-booking-{uuid4().hex[:8]}",
        email=f"frame-booking-{uuid4().hex[:8]}@example.com",
    )
    add_service(client, token, "Classic Cut", 45)
    target = (datetime.now(ZoneInfo("Pacific/Auckland")) + timedelta(days=9)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    time_phrase = f"{target.strftime('%A')} {target.day} {target.strftime('%B')} at 10 AM"
    db = DBSession()
    try:
        profile = client.get("/api/v1/salon", headers=auth(token))
        assert profile.status_code == 200
        tenant = await TenantRepository(db).by_slug(profile.json()["slug"])
        assert tenant is not None
        session = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id=f"frame-booking-{uuid4().hex}",
            caller_number="0215559876",
            called_number=tenant.phone_number,
        )
        await session.start()
        session.set_booking_spelled_name("Jane Example")
        session.ingest_booking_phone_fragment("0215559876")
        await session.append_transcript("caller", "My phone number is 0215559876")
        tools = VoiceToolset(db, tenant, session)
        handlers = create_voice_tool_handlers(session, tools)

        availability = HandlerParams(
            {
                "service_name": "Classic Cut",
                "starts_at_local": target.isoformat(),
                "staff_name": None,
                "caller_time_phrase": time_phrase,
            }
        )
        await handlers["check_availability"](availability)
        assert availability.results[-1][0]["ok"] is True
        assert availability.results[-1][0]["available"] is True
        assert datetime.fromisoformat(availability.results[-1][0]["starts_at"]) == target

        preparation = HandlerParams(
            {
                "service_name": "Classic Cut",
                "starts_at_local": target.isoformat(),
                "customer_name": "Jane Example",
                "customer_phone": "0215559876",
                "confirmed": False,
                "caller_time_phrase": time_phrase,
            }
        )
        await handlers["book_appointment"](preparation)
        prepared = preparation.results[-1][0]
        assert prepared["ok"] is True
        assert prepared["ready_to_confirm"] is True
        assert prepared["booked"] is False
        assert session.has_prepared_booking is True
        assert await tools.appointments.list() == []

        await session.append_transcript(
            "assistant",
            f"{prepared['spoken_start']}, Jane Example, {prepared['spoken_phone']}. Is that correct?",
        )
        await session.append_transcript("caller", caller_reply)
        frame = LLMContextFrame(LLMContext(messages=[{"role": "user", "content": caller_reply}]))
        latest = await latest_caller_text_from_frame(frame, session)
        handled = await handle_confirmation_turn(
            latest,
            call_session=session,
            tools=tools,
            speak=_speak,
            direction="downstream",
        )
        assert handled is expected_handled
        assert session.has_prepared_booking is expected_prepared

        appointments = [
            row for row in await tools.appointments.list() if row.call_id == session.call_id
        ]
        assert len(appointments) == expected_bookings
        if expected_bookings:
            assert appointments[0].status == "booked"
            assert appointments[0].customer_name == "Jane Example"
            assert appointments[0].customer_phone == "0215559876"
            assert appointments[0].starts_at.replace(tzinfo=UTC) == target.astimezone(UTC)
    finally:
        await db.close()
