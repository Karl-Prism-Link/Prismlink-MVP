from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.voice.confirmation import execute_confirmation_action
from services.voice.contact import is_explicit_affirmation, is_explicit_rejection
from services.voice.model_router import confirmation_fastpath_action


class FakeConfirmationSession:
    def __init__(self, prepared: str):
        self.voice_state = SimpleNamespace(
            intent=prepared,
            appointment_id="appointment-prepared-42",
            starts_at="2026-12-05T12:00:00+13:00",
            caller_time_phrase="Saturday at noon",
            message_target="Alex",
            confirmed=False,
            last_tool=None,
            last_tool_ok=None,
        )
        self.prepared = {
            "booking": prepared == "booking",
            "reschedule": prepared == "reschedule",
            "cancel": prepared == "cancel",
            "message": prepared == "message",
        }
        self.message_phone_cleared = False

    @property
    def has_prepared_booking(self):
        return self.prepared["booking"]

    @property
    def has_prepared_reschedule(self):
        return self.prepared["reschedule"]

    @property
    def has_prepared_cancel(self):
        return self.prepared["cancel"]

    @property
    def has_prepared_message(self):
        return self.prepared["message"]

    def update_voice_state(self, **changes):
        for key, value in changes.items():
            setattr(self.voice_state, key, value)

    def clear_prepared_booking(self):
        self.prepared["booking"] = False

    def clear_prepared_reschedule(self):
        self.prepared["reschedule"] = False

    def clear_prepared_cancel(self):
        self.prepared["cancel"] = False

    def clear_prepared_message(self):
        self.prepared["message"] = False

    def clear_message_phone(self):
        self.message_phone_cleared = True

    def clear_voice_state_fields(self, *names):
        for name in names:
            setattr(self.voice_state, name, None)


class FakeConfirmationTools:
    def __init__(self):
        self.calls = []

    async def book_appointment(self, *args):
        self.calls.append(("book_appointment", args))
        return {
            "ok": True,
            "booked": True,
            "appointment_id": "new-booking-99",
            "service": "Women's Cut",
            "starts_at": "2026-12-05T12:00:00+13:00",
            "customer_name": "Sam Taylor",
            "customer_phone": "0211234567",
            "spoken_start": "Saturday at noon",
        }

    async def reschedule_appointment(self, *args):
        self.calls.append(("reschedule_appointment", args))
        return {
            "ok": True,
            "rescheduled": True,
            "appointment_id": args[0],
            "service": "Colour",
            "starts_at": args[1],
            "spoken_start": "Saturday at noon",
        }

    async def cancel_appointment(self, *args):
        self.calls.append(("cancel_appointment", args))
        return {
            "ok": True,
            "cancelled": True,
            "appointment_id": args[0],
            "service": "Colour",
            "spoken_start": "Friday at eleven",
        }

    async def take_message(self, *args):
        self.calls.append(("take_message", args))
        return {"ok": True, "submitted": True, "target_staff": args[4]}


async def _speak_collector(spoken, text, direction):
    spoken.append((text, direction))


def _selected_action(session, text):
    return confirmation_fastpath_action(
        latest_user_text=text,
        has_prepared_booking=session.has_prepared_booking,
        has_prepared_reschedule=session.has_prepared_reschedule,
        has_prepared_cancel=session.has_prepared_cancel,
        has_prepared_message=session.has_prepared_message,
        is_affirmation=is_explicit_affirmation(text),
        is_rejection=is_explicit_rejection(text),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prepared", "expected_tool"),
    [
        ("booking", "book_appointment"),
        ("reschedule", "reschedule_appointment"),
        ("cancel", "cancel_appointment"),
        ("message", "take_message"),
    ],
)
async def test_bare_yes_commits_only_the_single_prepared_action(prepared, expected_tool):
    session = FakeConfirmationSession(prepared)
    tools = FakeConfirmationTools()
    spoken = []
    action = _selected_action(session, "yes")

    handled = await execute_confirmation_action(
        action,
        call_session=session,
        tools=tools,
        speak=lambda text, direction: _speak_collector(spoken, text, direction),
        direction="downstream",
    )

    assert handled is True
    assert len(tools.calls) == 1
    assert tools.calls[0][0] == expected_tool
    assert session.voice_state.confirmed is True
    assert session.voice_state.last_tool == expected_tool
    assert spoken
    if prepared == "reschedule":
        assert tools.calls[0][1] == (
            "appointment-prepared-42",
            "2026-12-05T12:00:00+13:00",
            True,
            "Saturday at noon",
        )
    elif prepared == "cancel":
        assert tools.calls[0][1] == ("appointment-prepared-42", True)
    elif prepared == "message":
        assert tools.calls[0][1] == ("", "", None, True, "Alex")
    else:
        assert tools.calls[0][1] == ("", "", "", "", True, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prepared", "expected_intent"),
    [
        ("booking", "booking"),
        ("reschedule", "reschedule"),
        ("cancel", "appointment_management"),
        ("message", "message"),
    ],
)
async def test_bare_no_rejects_prepared_action_without_calling_any_mutation(
    prepared, expected_intent
):
    session = FakeConfirmationSession(prepared)
    tools = FakeConfirmationTools()
    spoken = []
    action = _selected_action(session, "no")

    handled = await execute_confirmation_action(
        action,
        call_session=session,
        tools=tools,
        speak=lambda text, direction: _speak_collector(spoken, text, direction),
        direction="downstream",
    )

    assert handled is True
    assert tools.calls == []
    assert not any(session.prepared.values())
    assert session.voice_state.confirmed is False
    assert session.voice_state.intent == expected_intent
    assert spoken
    if prepared == "message":
        assert session.message_phone_cleared is True
        assert session.voice_state.message_text is None


def test_multiple_prepared_actions_never_accept_a_bare_yes_or_no():
    session = FakeConfirmationSession("booking")
    session.prepared["cancel"] = True

    assert _selected_action(session, "yes") is None
    assert _selected_action(session, "no") is None


@pytest.mark.parametrize("text", ["no, make it four", "yes, but change the time", "not yet"])
def test_non_bare_confirmation_language_is_not_committed_by_fastpath(text):
    session = FakeConfirmationSession("booking")

    assert _selected_action(session, text) is None
