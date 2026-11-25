from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.voice.tool_handlers import create_voice_tool_handlers


class FakeSession:
    def __init__(self):
        self.voice_state = SimpleNamespace(
            intent="unknown",
            appointment_id=None,
            customer_name=None,
            booking_spelled_name="Sam Taylor",
            booking_phone_digits="0211234567",
        )
        self.transcripts = []
        self.updates = []

    @property
    def booking_spelled_name(self):
        return self.voice_state.booking_spelled_name

    @property
    def booking_phone_digits(self):
        return self.voice_state.booking_phone_digits

    def update_voice_state(self, **changes):
        self.updates.append(changes)
        for key, value in changes.items():
            setattr(self.voice_state, key, value)

    async def append_transcript(self, role, text):
        self.transcripts.append((role, text))


class FakeLLM:
    def __init__(self):
        self.frames = []

    async def push_frame(self, frame):
        self.frames.append(frame)


class FakeParams:
    def __init__(self, arguments):
        self.arguments = arguments
        self.results = []
        self.llm = FakeLLM()

    async def result_callback(self, result, properties=None):
        self.results.append((result, properties))


class FakeTools:
    def __init__(self):
        self.calls = []
        self.booking_result = {
            "ok": True,
            "ready_to_confirm": True,
            "service": "Women's Cut",
            "starts_at": "2026-12-04T10:00:00+13:00",
            "customer_name": "Sam Taylor",
            "customer_phone": "0211234567",
            "appointment_id": "apt-1",
        }
        self.find_result = {
            "ok": True,
            "identity_verified": True,
            "verified_customer_phone": "0211234567",
            "appointments": [
                {
                    "appointment_id": "apt-2",
                    "service": "Colour",
                    "starts_at": "2026-12-04T11:00:00+13:00",
                    "customer_name": "Sam Taylor",
                }
            ],
        }

    async def check_availability(self, *args):
        self.calls.append(("check_availability", args))
        return {
            "ok": True,
            "available": True,
            "service": args[0],
            "staff": args[2],
            "caller_time_phrase": args[3],
            "starts_at": "2026-12-04T10:00:00+13:00",
            "appointment_id": None,
        }

    async def check_reschedule_availability(self, *args):
        self.calls.append(("check_reschedule_availability", args))
        return {
            "ok": True,
            "available": True,
            "service": "Colour",
            "caller_time_phrase": args[2],
            "starts_at": "2026-12-05T12:00:00+13:00",
            "appointment_id": args[0],
        }

    async def book_appointment(self, *args):
        self.calls.append(("book_appointment", args))
        return {**self.booking_result, "booked": bool(args[4]), "ok": True}

    async def find_appointments(self, *args):
        self.calls.append(("find_appointments", args))
        return self.find_result

    async def cancel_appointment(self, *args):
        self.calls.append(("cancel_appointment", args))
        return {
            "ok": True,
            "cancelled": bool(args[1]),
            "service": "Colour",
            "starts_at": "2026-12-04T11:00:00+13:00",
            "appointment_id": args[0],
        }

    async def reschedule_appointment(self, *args):
        self.calls.append(("reschedule_appointment", args))
        return {
            "ok": True,
            "rescheduled": bool(args[2]),
            "service": "Colour",
            "starts_at": "2026-12-05T12:00:00+13:00",
            "appointment_id": args[0],
        }

    async def take_message(self, *args):
        self.calls.append(("take_message", args))
        return {
            "ok": True,
            "submitted": bool(args[3]),
            "message": args[0],
            "customer_phone": args[1],
            "customer_name": args[2],
            "target_staff": args[4],
        }


@pytest.mark.asyncio
async def test_mocked_llm_booking_tool_calls_preserve_captured_contact_and_confirmation():
    session, tools = FakeSession(), FakeTools()
    handlers = create_voice_tool_handlers(session, tools)

    availability = FakeParams(
        {
            "service_name": "Women's Cut",
            "starts_at_local": "Friday 10am",
            "staff_name": None,
            "caller_time_phrase": "Friday at ten",
        }
    )
    await handlers["check_availability"](availability)
    assert availability.results[0][0]["available"] is True

    booking = FakeParams(
        {
            "service_name": "Women's Cut",
            "starts_at_local": "2026-12-04T10:00:00+13:00",
            "customer_name": "Wrong LLM Name",
            "customer_phone": "000",
            "confirmed": False,
        }
    )
    await handlers["book_appointment"](booking)
    assert tools.calls[-1][1][2:5] == ("Sam Taylor", "0211234567", False)
    assert booking.results[0][0]["booked"] is False

    confirmation = FakeParams(
        {
            "service_name": "Women's Cut",
            "starts_at_local": "2026-12-04T10:00:00+13:00",
            "customer_name": "Wrong LLM Name",
            "customer_phone": "000",
            "confirmed": True,
        }
    )
    await handlers["book_appointment"](confirmation)
    assert tools.calls[-1][1][4] is True
    assert confirmation.results[0][0]["booked"] is True
    assert session.voice_state.last_tool == "book_appointment"


@pytest.mark.asyncio
async def test_verified_single_appointment_cancel_is_prepared_before_explicit_commit():
    session, tools = FakeSession(), FakeTools()
    session.voice_state.intent = "cancel"
    handlers = create_voice_tool_handlers(session, tools)

    lookup = FakeParams({"customer_phone": "0211234567"})
    await handlers["find_appointments"](lookup)
    assert ("cancel_appointment", ("apt-2", False)) in tools.calls
    assert lookup.results[0][0]["cancelled"] is False

    commit = FakeParams({"appointment_id": "apt-2", "confirmed": True})
    await handlers["cancel_appointment"](commit)
    assert tools.calls[-1] == ("cancel_appointment", ("apt-2", True))
    assert commit.results[0][0]["cancelled"] is True
    assert commit.results[0][1].run_llm is False
    assert session.transcripts and session.transcripts[-1][0] == "assistant"
    assert session.voice_state.confirmed is True


@pytest.mark.asyncio
async def test_reschedule_success_uses_authoritative_result_without_llm_rewrite():
    session, tools = FakeSession(), FakeTools()
    handlers = create_voice_tool_handlers(session, tools)
    params = FakeParams(
        {
            "appointment_id": "apt-2",
            "starts_at_local": "Saturday noon",
            "caller_time_phrase": "Saturday at noon",
            "confirmed": True,
        }
    )

    await handlers["reschedule_appointment"](params)

    assert tools.calls == [
        ("reschedule_appointment", ("apt-2", "Saturday noon", True, "Saturday at noon"))
    ]
    assert params.results[0][0]["rescheduled"] is True
    assert params.results[0][1].run_llm is False
    assert len(params.llm.frames) == 1
    assert session.transcripts[-1][0] == "assistant"


@pytest.mark.asyncio
async def test_message_submission_is_recorded_only_after_confirmed_tool_call():
    session, tools = FakeSession(), FakeTools()
    handlers = create_voice_tool_handlers(session, tools)
    prepare = FakeParams(
        {
            "message": "Please call me back",
            "customer_phone": "0211234567",
            "customer_name": "Sam",
            "confirmed": False,
            "target_staff": "Alex",
        }
    )
    await handlers["take_message"](prepare)
    assert prepare.results[0][0]["submitted"] is False
    assert not session.transcripts

    submit = FakeParams(
        {
            "message": "Please call me back",
            "customer_phone": "0211234567",
            "customer_name": "Sam",
            "confirmed": True,
            "target_staff": "Alex",
        }
    )
    await handlers["take_message"](submit)
    assert tools.calls[-1][1][3] is True
    assert submit.results[0][0]["submitted"] is True
    assert submit.results[0][1].run_llm is False
    assert session.transcripts[-1] == ("assistant", "I've left your message for Alex.")
    assert len(submit.llm.frames) == 1


@pytest.mark.asyncio
async def test_reschedule_availability_uses_selected_appointment_not_llm_service():
    session, tools = FakeSession(), FakeTools()
    session.voice_state.intent = "appointment_management"
    session.voice_state.appointment_id = "apt-2"
    handlers = create_voice_tool_handlers(session, tools)
    params = FakeParams(
        {
            "service_name": "Wrong Service",
            "starts_at_local": "Saturday noon",
            "caller_time_phrase": "Saturday at noon",
        }
    )

    await handlers["check_availability"](params)

    assert tools.calls[0][0] == "check_reschedule_availability"
    assert tools.calls[0][1][0] == "apt-2"
    assert session.voice_state.intent == "reschedule"
    assert params.results[0][0]["service"] == "Colour"
