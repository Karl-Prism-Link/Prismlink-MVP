from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from database.repositories import TenantRepository
from database.session import DBSession
from services.voice.session import LiveCallSession
from services.voice.tools import VoiceToolset


def register(client, *, name: str, slug: str, email: str):
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


def auth(token: str):
    return {"Authorization": f"Bearer {token}"}


def add_service(client, token: str, name: str, duration: int):
    response = client.post(
        "/api/v1/salon/services",
        headers=auth(token),
        json={"name": name, "duration_minutes": duration},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_appointment(client, token: str, service_id: str, start: datetime, phone: str):
    response = client.post(
        "/api/v1/appointments",
        headers=auth(token),
        json={
            "customer_name": "Jane Doe",
            "customer_phone": phone,
            "service_id": service_id,
            "starts_at": start.astimezone(UTC).isoformat(),
            "idempotency_key": f"reschedule-hardening-{start.timestamp()}",
            "confirmed": True,
            "source": "voice",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_find_appointments_reports_caller_day_mismatch_with_actual_details(client):
    token = register(
        client,
        name="Lookup Guard Salon",
        slug="lookup-guard-salon",
        email="lookup-guard@example.com",
    )
    service = add_service(client, token, "Classic Cut", 45)
    tz = ZoneInfo("Pacific/Auckland")
    local_start = (datetime.now(tz) + timedelta(days=5)).replace(
        hour=14, minute=0, second=0, microsecond=0
    )
    created = create_appointment(client, token, service["id"], local_start, "0213333333")
    actual_weekday = local_start.strftime("%A")
    wrong_weekday = (local_start + timedelta(days=1)).strftime("%A")

    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("lookup-guard-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="reschedule-lookup-guard",
            caller_number="0213333333",
            called_number=tenant.phone_number,
        )
        await live.start()
        tools = VoiceToolset(db, tenant, live)

        await live.append_transcript("caller", "My booking phone is 0213333333")
        result = await tools.find_appointments(
            "0213333333",
            caller_time_phrase=f"my appointment on {wrong_weekday}",
        )
        assert result["ok"] is False
        assert result["code"] == "APPOINTMENT_REFERENCE_MISMATCH"
        assert len(result["appointments"]) == 1
        found = result["appointments"][0]
        assert found["appointment_id"] == created["id"]
        assert found["service"] == "Classic Cut"
        assert found["weekday"] == actual_weekday
        assert actual_weekday in found["spoken_start"]
    finally:
        await db.close()


async def test_reschedule_preflight_uses_existing_service_duration_and_requires_preparation(client):
    token = register(
        client,
        name="Reschedule Guard Salon",
        slug="reschedule-guard-salon",
        email="reschedule-guard@example.com",
    )
    service = add_service(client, token, "Long Cut", 45)
    tz = ZoneInfo("Pacific/Auckland")
    old_start = (datetime.now(tz) + timedelta(days=6)).replace(
        hour=14, minute=0, second=0, microsecond=0
    )
    new_start = (datetime.now(tz) + timedelta(days=7)).replace(
        hour=13, minute=0, second=0, microsecond=0
    )
    created = create_appointment(client, token, service["id"], old_start, "0214444444")
    phrase = f"{new_start.strftime('%A')} at 1 PM"

    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("reschedule-guard-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="reschedule-preflight-guard",
            caller_number="0214444444",
            called_number=tenant.phone_number,
        )
        await live.start()
        tools = VoiceToolset(db, tenant, live)

        # A destructive management action first requires a caller-spoken booking phone.
        await live.append_transcript("caller", "My booking phone is 0214444444")
        # A direct confirmed mutation without a prepared availability/readback is rejected.
        await live.append_transcript("caller", "Yes")
        denied = await tools.reschedule_appointment(
            created["id"],
            new_start.isoformat(),
            True,
            phrase,
        )
        assert denied["ok"] is False
        assert denied["code"] == "RESCHEDULE_PRECHECK_REQUIRED"

        prepared = await tools.check_reschedule_availability(
            created["id"],
            new_start.isoformat(),
            phrase,
        )
        assert prepared["ok"] is True
        assert prepared["available"] is True
        assert prepared["ready_to_confirm"] is True
        assert prepared["service"] == "Long Cut"
        assert prepared["duration_minutes"] == 45
        assert datetime.fromisoformat(prepared["ends_at"]) - datetime.fromisoformat(
            prepared["starts_at"]
        ) == timedelta(minutes=45)
        assert old_start.strftime("%A") in prepared["current_spoken_start"]
        assert new_start.strftime("%A") in prepared["requested_spoken_start"]

        await live.append_transcript(
            "assistant",
            (
                f"Move {prepared['service']} from {prepared['current_spoken_start']} "
                f"to {prepared['requested_spoken_start']}?"
            ),
        )
        await live.append_transcript("caller", "Yes")
        moved = await tools.reschedule_appointment(
            created["id"],
            new_start.isoformat(),
            True,
            phrase,
        )
        assert moved["ok"] is True
        assert moved["rescheduled"] is True
        assert moved["status"] == "rescheduled"
        assert moved["service"] == "Long Cut"
        assert datetime.fromisoformat(moved["ends_at"]) - datetime.fromisoformat(
            moved["starts_at"]
        ) == timedelta(minutes=45)
        assert "rescheduled" in moved["instruction"].casefold()
        assert "not booked" in moved["instruction"].casefold()
    finally:
        await db.close()
