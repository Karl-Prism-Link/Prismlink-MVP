from database.repositories import TenantRepository
from database.session import DBSession
from services.voice.contact import (
    is_management_phone_recovery_statement,
    is_management_phone_unavailable,
)
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


def test_management_phone_unavailable_detector_is_conservative():
    assert is_management_phone_unavailable("I can't remember it.") is True
    assert is_management_phone_unavailable("I don't remember the number") is True
    assert is_management_phone_unavailable("I forgot the number") is True
    assert is_management_phone_unavailable("Can you look up Tom?") is False
    assert is_management_phone_unavailable("My number is 0213333333") is False
    assert is_management_phone_recovery_statement("Call me on 0213333333") is False
    assert (
        is_management_phone_recovery_statement(
            "Actually I remember it, the booking number is 0213333333"
        )
        is True
    )


async def test_identity_fallback_stops_reasking_after_caller_cannot_remember(client):
    register(
        client,
        name="Fallback Salon",
        slug="fallback-salon",
        email="fallback@example.com",
    )
    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("fallback-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="fallback",
            caller_number="0213333333",
            called_number=tenant.phone_number,
        )
        await live.start()
        tools = VoiceToolset(db, tenant, live)

        await live.append_transcript("caller", "I need to cancel my appointment")
        first = await tools.find_appointments("0213333333")
        assert first["code"] == "IDENTITY_VERIFICATION_REQUIRED"

        live.update_voice_state(intent="cancel", management_phone_unavailable=True)
        await live.append_transcript("caller", "I can't remember it")
        second = await tools.find_appointments("0213333333")
        assert second["ok"] is False
        assert second["code"] == "IDENTITY_PHONE_UNAVAILABLE"
        assert "do not ask" in second["instruction"].casefold()
        assert "callback" in second["instruction"].casefold()
        assert "appointments" not in second
    finally:
        await db.close()


async def test_spoken_booking_phone_reenables_management_after_fallback(client):
    register(
        client,
        name="Recovery Salon",
        slug="recovery-salon",
        email="recovery@example.com",
    )
    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("recovery-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="recovery",
            caller_number="0213333333",
            called_number=tenant.phone_number,
        )
        await live.start()
        tools = VoiceToolset(db, tenant, live)

        live.update_voice_state(intent="cancel", management_phone_unavailable=True)

        # A callback number after fallback is not booking identity.
        await live.append_transcript("caller", "Call me on 0214444444")
        callback_only = await tools.find_appointments("0214444444")
        assert callback_only["ok"] is False
        assert callback_only["code"] == "IDENTITY_PHONE_UNAVAILABLE"
        assert live.verified_management_phone is None

        # Explicitly recovering the booking number re-enables appointment management.
        await live.append_transcript(
            "caller",
            "Actually I remember it, the booking number is 0213333333",
        )
        result = await tools.find_appointments("0213333333")
        assert result["ok"] is True
        assert result["identity_verified"] is True
        assert live.verified_management_phone == "0213333333"
        assert live.voice_state.management_phone_unavailable is False
    finally:
        await db.close()
