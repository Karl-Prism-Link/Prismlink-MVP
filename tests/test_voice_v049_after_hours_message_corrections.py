from pathlib import Path

from database.repositories import MessageRepository, TenantRepository
from database.session import DBSession
from services.voice.contact import extract_phone_from_speech
from services.voice.message_routing import (
    callback_message_from_speech,
    extract_message_correction,
    is_callback_request,
    message_caller_name_from_speech,
    message_target_from_speech,
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


def test_callback_request_does_not_treat_callback_name_as_staff_target():
    text = "Can you get them to ring Georgie at o two one three eight six four eight six one."
    assert is_callback_request(text) is True
    assert message_caller_name_from_speech(text) == "Georgie"
    assert callback_message_from_speech(text, caller_name="Georgie") == "Please call Georgie back."
    assert message_target_from_speech(text, ("Alex", "Jo")) is None
    phone = extract_phone_from_speech(text, min_digits=9, max_digits=12)
    assert phone is not None
    assert phone.digits == "0213864861"


def test_callback_message_uses_known_caller_name_for_ring_me_wording():
    assert (
        callback_message_from_speech("Just get them to ring me, please.", caller_name="Georgie")
        == "Please call Georgie back."
    )


def test_message_name_and_text_corrections_are_explicitly_detected():
    assert message_caller_name_from_speech("I'm Georgie.") == "Georgie"
    assert extract_message_correction("The message should just say ring Georgie.") == "ring Georgie"


def test_v049_runtime_has_generic_booking_and_message_correction_fastpaths():
    source = (Path(__file__).parents[1] / "apps" / "voice" / "bot.py").read_text()
    assert "PRISM LINK deterministic generic booking intent fastpath" in source
    assert 'await self._speak("Sure, what service would you like to book?"' in source
    assert "PRISM LINK deterministic message correction prepared" in source
    assert "message_customer_name" in source


async def test_confirmed_message_can_be_corrected_without_duplicate_row(client):
    register(
        client,
        name="Message Correction Salon",
        slug="message-correction-salon",
        email="message-correction@example.com",
    )
    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("message-correction-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="message-correction-live-001",
            caller_number="0213864861",
            called_number=tenant.phone_number,
        )
        await live.start()
        tools = VoiceToolset(db, tenant, live)

        prepared = await tools.take_message(
            "Please call Georgie back.",
            "0213864861",
            "Georgie",
            False,
            None,
        )
        assert prepared["ready_to_confirm"] is True
        await live.append_transcript("caller", "Confirm")
        first = await tools.take_message("", "", None, True, None)
        assert first["submitted"] is True

        corrected = await tools.take_message(
            "ring Georgie",
            "0213864861",
            "Georgie",
            False,
            None,
        )
        assert corrected["ready_to_confirm"] is True
        await live.append_transcript("caller", "Yes")
        second = await tools.take_message("", "", None, True, None)
        assert second["submitted"] is True

        rows = await MessageRepository(db, tenant.id).list()
        assert len(rows) == 1
        assert rows[0].customer_name == "Georgie"
        assert rows[0].callback_phone == "0213864861"
        assert rows[0].message_text == "ring Georgie"
    finally:
        await db.close()
