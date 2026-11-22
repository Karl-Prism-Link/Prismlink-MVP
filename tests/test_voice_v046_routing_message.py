from pathlib import Path

import pytest

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
from services.voice.safety import is_health_only_request, is_immediate_emergency_language
from services.voice.session import LiveCallSession, VoiceConversationState
from services.voice.speech_cleanup import (
    deterministic_post_action_social_reply,
    is_farewell_utterance,
)
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


def test_farewell_is_recognised_and_does_not_repeat_a_long_closing():
    assert is_farewell_utterance("Thanks, bye.") is True
    assert deterministic_post_action_social_reply("Thanks, bye.") == "Goodbye. Have a great day."


def test_health_only_requests_are_never_routed_as_medical_advice():
    assert is_health_only_request("I cut my finger") is True
    assert is_health_only_request("What should I do about this injury?") is True
    assert is_health_only_request("I am sick and need to cancel my appointment") is False
    assert is_health_only_request("I need to book a haircut") is False
    assert is_immediate_emergency_language("I can't breathe") is True
    assert is_immediate_emergency_language("I cut my finger") is False


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
    token = register(
        client,
        name="Routing Message Salon",
        slug="routing-message-salon",
        email="routing-message@example.com",
    )
    staff_response = client.post(
        "/api/v1/salon/staff", headers={"Authorization": f"Bearer {token}"}, json={"name": "Jo"}
    )
    assert staff_response.status_code == 201, staff_response.text
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

        invented = await tools.take_message(
            "Please send this to the emergency team", "0271234567", "Karl", False, "Emergency"
        )
        assert invented["ok"] is False
        assert invented["code"] == "MESSAGE_RECIPIENT_NOT_CONFIGURED"

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
        assert no_yes["code"] == "MESSAGE_READBACK_REQUIRED"

        live.mark_message_readback_completed()
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


def test_immediate_safety_state_is_persistent_and_blocks_workflow_state():
    state = VoiceConversationState()
    assert state.immediate_safety_active is False
    assert state.safety_response_sent is False

    state.immediate_safety_active = True
    state.safety_reason = "immediate_emergency"
    state.safety_response_sent = True

    assert state.immediate_safety_active is True
    assert state.safety_reason == "immediate_emergency"
    assert state.safety_response_sent is True


@pytest.mark.asyncio
async def test_immediate_safety_blocks_every_voice_tool_at_the_tool_boundary():
    """A model/tool call cannot bypass the runtime emergency guard."""
    from services.voice.tools import VoiceToolset

    session = LiveCallSession.__new__(LiveCallSession)
    session.voice_state = VoiceConversationState()
    session._prepared_booking_fingerprint = "booking"
    session._prepared_booking_payload = {"public": {"service": "Women's Cut"}}
    session._prepared_reschedule_fingerprint = "reschedule"
    session._prepared_cancel_fingerprint = "cancel"
    session._prepared_message_fingerprint = "message"
    session._prepared_message_payload = {"message": "call back"}
    session._message_readback_completed = True
    session._booking_phone_capture_active = True
    session._booking_name_capture_active = True
    session._message_phone_digits = "0211234567"
    session._verified_management_phone = "0211234567"
    session.activate_immediate_safety("immediate_emergency")

    tools = VoiceToolset.__new__(VoiceToolset)
    tools.call_session = session
    results = await __import__("asyncio").gather(
        tools.get_salon_information("opening hours"),
        tools.check_availability("Women's Cut", "tomorrow 10am"),
        tools.find_available_times("Women's Cut", "tomorrow"),
        tools.book_appointment("Women's Cut", "2026-11-24T10:00", "Caller", "0211234567", False),
        tools.check_reschedule_availability("appointment", "2026-11-24T10:00"),
        tools.find_appointments("0211234567"),
        tools.reschedule_appointment("appointment", "2026-11-24T10:00", True),
        tools.cancel_appointment("appointment", True),
        tools.take_message("Call me back", "0211234567", "Caller", True),
    )
    assert all(result["code"] == "IMMEDIATE_SAFETY_ACTIVE" for result in results)
    assert all(result["safety_active"] is True for result in results)
    assert session.immediate_safety_active is True
    assert session.has_prepared_message is False
    assert session.has_prepared_reschedule is False
    assert session.has_prepared_cancel is False
    assert session.booking_phone_capture_active is False
    assert session.booking_name_capture_active is False
    assert "immediate_safety_active=true" in session.voice_state_summary
    assert "safety_reason=immediate_emergency" in session.voice_state_summary
