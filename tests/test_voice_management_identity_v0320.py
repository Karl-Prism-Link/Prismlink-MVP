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


def create_appointment(
    client, token: str, service_id: str, start: datetime, phone: str, name: str = "Jane Doe"
):
    response = client.post(
        "/api/v1/appointments",
        headers=auth(token),
        json={
            "customer_name": name,
            "customer_phone": phone,
            "service_id": service_id,
            "starts_at": start.astimezone(UTC).isoformat(),
            "idempotency_key": f"v0320-{phone}-{start.timestamp()}",
            "confirmed": True,
            "source": "voice",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_caller_id_and_date_are_not_identity_proof(client):
    token = register(
        client, name="Identity Salon", slug="identity-salon", email="identity@example.com"
    )
    service = add_service(client, token, "Blow Wave", 45)
    tz = ZoneInfo("Pacific/Auckland")
    start = (datetime.now(tz) + timedelta(days=5)).replace(
        hour=15, minute=0, second=0, microsecond=0
    )
    create_appointment(client, token, service["id"], start, "0213333333", "Jane Doe")

    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("identity-salon")
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="identity-proof",
            caller_number="0213333333",
            called_number=tenant.phone_number,
        )
        await live.start()
        tools = VoiceToolset(db, tenant, live)

        await live.append_transcript("caller", f"My appointment is {start.strftime('%A')} at 3 PM")
        result = await tools.find_appointments(
            "0213333333", caller_time_phrase=f"{start.strftime('%A')} at 3 PM"
        )
        assert result["ok"] is False
        assert result["code"] == "IDENTITY_VERIFICATION_REQUIRED"
        assert "appointments" not in result
        assert "Jane" not in str(result)
    finally:
        await db.close()


async def test_wrong_claimed_name_cannot_be_bypassed_with_date_followup(client):
    token = register(
        client, name="Name Guard Salon", slug="name-guard-salon", email="name-guard@example.com"
    )
    service = add_service(client, token, "Blow Wave", 45)
    tz = ZoneInfo("Pacific/Auckland")
    start = (datetime.now(tz) + timedelta(days=6)).replace(
        hour=15, minute=0, second=0, microsecond=0
    )
    create_appointment(client, token, service["id"], start, "0219999999", "Jane Doe")

    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("name-guard-salon")
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="name-guard",
            caller_number="0219999999",
            called_number=tenant.phone_number,
        )
        await live.start()
        tools = VoiceToolset(db, tenant, live)

        live.update_voice_state(customer_name="Tom")
        await live.append_transcript("caller", "Tom, phone 0219999999")
        first = await tools.find_appointments("0219999999", customer_name="Tom")
        assert first["ok"] is True
        assert first["identity_verified"] is True
        assert first["appointments"] == []

        await live.append_transcript("caller", f"{start.strftime('%A')} at 3 PM")
        second = await tools.find_appointments(
            "0219999999", caller_time_phrase=f"{start.strftime('%A')} at 3 PM"
        )
        assert second["ok"] is True
        assert second["appointments"] == []
        assert "Jane" not in str(second)
    finally:
        await db.close()


async def test_cancel_rejects_appointment_from_different_verified_phone(client):
    token = register(
        client, name="Ownership Salon", slug="ownership-salon", email="ownership@example.com"
    )
    service = add_service(client, token, "Men's Cut", 30)
    tz = ZoneInfo("Pacific/Auckland")
    start = (datetime.now(tz) + timedelta(days=7)).replace(
        hour=14, minute=0, second=0, microsecond=0
    )
    created = create_appointment(client, token, service["id"], start, "0211111111", "Jane Doe")

    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("ownership-salon")
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="ownership",
            caller_number="0212222222",
            called_number=tenant.phone_number,
        )
        await live.start()
        tools = VoiceToolset(db, tenant, live)
        await live.append_transcript("caller", "My booking phone is 0212222222")
        result = await tools.cancel_appointment(created["id"], False)
        assert result["ok"] is False
        assert result["code"] == "APPOINTMENT_OWNERSHIP_MISMATCH"
        assert "service" not in result
        assert "spoken_start" not in result
    finally:
        await db.close()
