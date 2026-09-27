from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from database.repositories import TenantRepository
from database.session import DBSession
from services.voice.contact import is_explicit_rejection
from services.voice.session import LiveCallSession
from services.voice.speech_cleanup import cancellation_success_speech, reschedule_success_speech
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
            "idempotency_key": f"v0319-{start.timestamp()}",
            "confirmed": True,
            "source": "voice",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_explicit_rejection_is_conservative():
    assert is_explicit_rejection("No.") is True
    assert is_explicit_rejection("Nope, not that one") is True
    assert is_explicit_rejection("Yes") is False
    assert is_explicit_rejection("Three PM") is False


def test_deterministic_management_success_speech():
    moved = reschedule_success_speech(
        {"service": "Blow Wave", "spoken_start": "Monday 17 August 2026 at 3:00 PM"}
    )
    cancelled = cancellation_success_speech(
        {"service": "Men's Cut", "spoken_start": "Tuesday 18 August 2026 at 1:00 PM"}
    )
    assert moved == "Your Blow Wave appointment has been moved to Monday 17 August 2026 at 3:00 PM."
    assert (
        cancelled
        == "Your Men's Cut appointment on Tuesday 18 August 2026 at 1:00 PM has been cancelled."
    )


async def test_rejected_lookup_candidate_is_not_represented_as_selected(client):
    token = register(
        client,
        name="Rejected Lookup Salon",
        slug="rejected-lookup-salon",
        email="rejected-lookup@example.com",
    )
    service = add_service(client, token, "Blow Wave", 45)
    tz = ZoneInfo("Pacific/Auckland")
    start = (datetime.now(tz) + timedelta(days=3)).replace(
        hour=13, minute=0, second=0, microsecond=0
    )
    created = create_appointment(client, token, service["id"], start, "0213333333")

    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("rejected-lookup-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="rejected-lookup",
            caller_number="0213333333",
            called_number=tenant.phone_number,
        )
        await live.start()
        tools = VoiceToolset(db, tenant, live)

        await live.append_transcript("caller", "My booking phone is 0213333333")
        initial = await tools.find_appointments("0213333333")
        assert initial["ok"] is True
        assert initial["appointments"][0]["appointment_id"] == created["id"]

        live.reject_appointment(created["id"])
        result = await tools.find_appointments("0213333333", caller_time_phrase="Monday at 2:30 PM")
        assert result["ok"] is False
        assert result["code"] in {
            "APPOINTMENT_REFERENCE_MISMATCH",
            "ONLY_REJECTED_APPOINTMENTS_REMAIN",
        }
        assert all(
            item["appointment_id"] != created["id"] for item in result.get("appointments", [])
        )
        assert any(
            item["appointment_id"] == created["id"]
            for item in result.get("rejected_appointments", [])
        )
        assert "do not ask" in result["instruction"].casefold()
    finally:
        await db.close()


async def test_cancel_requires_prepare_then_fresh_yes(client):
    token = register(
        client,
        name="Cancel Guard Salon",
        slug="cancel-guard-salon",
        email="cancel-guard@example.com",
    )
    service = add_service(client, token, "Men's Cut", 30)
    tz = ZoneInfo("Pacific/Auckland")
    start = (datetime.now(tz) + timedelta(days=4)).replace(
        hour=14, minute=0, second=0, microsecond=0
    )
    created = create_appointment(client, token, service["id"], start, "0217777777")

    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("cancel-guard-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="cancel-guard",
            caller_number="0217777777",
            called_number=tenant.phone_number,
        )
        await live.start()
        tools = VoiceToolset(db, tenant, live)

        await live.append_transcript("caller", "Cancel my appointment")
        identity_denied = await tools.cancel_appointment(created["id"], True)
        assert identity_denied["ok"] is False
        assert identity_denied["code"] == "IDENTITY_VERIFICATION_REQUIRED"

        await live.append_transcript("caller", "My booking phone is 0217777777")
        denied = await tools.cancel_appointment(created["id"], True)
        assert denied["ok"] is False
        assert denied["code"] == "CANCEL_PRECHECK_REQUIRED"

        prepared = await tools.cancel_appointment(created["id"], False)
        assert prepared["ok"] is True
        assert prepared["ready_to_confirm"] is True
        assert prepared["service"] == "Men's Cut"
        assert prepared["spoken_start"]

        # An unrelated caller turn is not sufficient confirmation.
        await live.append_transcript("caller", "Three PM")
        no_yes = await tools.cancel_appointment(created["id"], True)
        assert no_yes["ok"] is False
        assert no_yes["code"] == "CONFIRMATION_REQUIRED"

        await live.append_transcript(
            "assistant",
            f"Cancel your {prepared['service']} on {prepared['spoken_start']}?",
        )
        await live.append_transcript("caller", "Yes")
        cancelled = await tools.cancel_appointment(created["id"], True)
        assert cancelled["ok"] is True
        assert cancelled["cancelled"] is True
        assert cancelled["status"] == "cancelled"
        assert cancelled["service"] == "Men's Cut"
    finally:
        await db.close()
