from pathlib import Path

from database.repositories import MessageRepository, TenantRepository
from database.session import DBSession
from services.voice.contact import is_explicit_affirmation, is_explicit_rejection
from services.voice.message_routing import (
    extract_inline_message,
    is_message_request,
    match_configured_staff_name,
    requested_person_from_speech,
    routing_fallback_speech,
)
from services.voice.model_router import confirmation_fastpath_action
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


def test_direct_routing_phrases_extract_named_person():
    assert requested_person_from_speech("Hi. Can I please speak to Alex?") == "Alex"
    assert requested_person_from_speech("Can I please speak to jo?") == "Jo"
    assert requested_person_from_speech("Put me through to Sarah now") == "Sarah"


def test_staff_match_is_configured_and_conservative():
    staff = ("Jo", "Sarah King")
    assert match_configured_staff_name("Jo", staff) == "Jo"
    assert match_configured_staff_name("Sarah King", staff) == "Sarah King"
    assert match_configured_staff_name("Alex", staff) is None


def test_message_request_and_inline_message_are_detected():
    assert is_message_request("Can you leave a message?") is True
    assert (
        extract_inline_message("Please leave a message for Jo saying call me back tomorrow")
        == "call me back tomorrow"
    )


def test_routing_fallback_does_not_claim_transfer_success():
    speech = routing_fallback_speech("Jo", matched_staff=True)
    assert "can't connect you directly" in speech
    assert "message for Jo" in speech


def test_prepared_message_bare_yes_is_deterministic():
    text = "Yes."
    action = confirmation_fastpath_action(
        latest_user_text=text,
        has_prepared_booking=False,
        has_prepared_reschedule=False,
        has_prepared_cancel=False,
        has_prepared_message=True,
        is_affirmation=is_explicit_affirmation(text),
        is_rejection=is_explicit_rejection(text),
    )
    assert action == "message_confirm"


def test_v046_runtime_has_deterministic_routing_and_message_paths():
    source = (Path(__file__).parents[1] / "apps" / "voice" / "bot.py").read_text()
    assert "async def _handle_routing_and_message" in source
    assert "PRISM LINK deterministic routing fallback" in source
    assert "PRISM LINK deterministic message intent fastpath" in source
    assert "has_prepared_message=call_session.has_prepared_message" in source


async def test_message_tool_requires_prepare_and_fresh_yes(client):
    register(
        client,
        name="Routing Message Salon",
        slug="routing-message-salon",
        email="routing-message@example.com",
    )
    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("routing-message-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="routing-message-live-001",
            caller_number="0271234567",
            called_number=tenant.phone_number,
        )
        await live.start()
        tools = VoiceToolset(db, tenant, live)

        missing = await tools.take_message("", "", None, True, "Jo")
        assert missing["ok"] is False
        assert missing["code"] == "MESSAGE_PRECHECK_REQUIRED"

        prepared = await tools.take_message(
            "Please call me back about my appointment",
            "0271234567",
            "Karl",
            False,
            "Jo",
        )
        assert prepared["ok"] is True
        assert prepared["ready_to_confirm"] is True
        assert await MessageRepository(db, tenant.id).list() == []

        no_yes = await tools.take_message("", "", None, True, "Jo")
        assert no_yes["ok"] is False
        assert no_yes["code"] == "CONFIRMATION_REQUIRED"

        await live.append_transcript("caller", "Yes")
        submitted = await tools.take_message("", "", None, True, "Jo")
        assert submitted["ok"] is True
        assert submitted["submitted"] is True

        rows = await MessageRepository(db, tenant.id).list()
        assert len(rows) == 1
        assert rows[0].callback_phone == "0271234567"
        assert rows[0].message_text == "For Jo: Please call me back about my appointment"
        assert live.resolution is not None
        assert live.resolution.outcome == "message_taken"
    finally:
        await db.close()
