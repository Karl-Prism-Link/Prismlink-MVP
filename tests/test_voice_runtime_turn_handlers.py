from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.voice.turn_handlers import handle_common_enquiry


class FakeCallSession:
    def __init__(self):
        self.voice_state = SimpleNamespace(intent=None, last_tool=None)
        self.recent_caller_transcripts = []
        self.cleared = []
        self.updates = []
        self.resolutions = []

    def clear_prepared_booking(self):
        self.cleared.append("booking")

    def clear_prepared_reschedule(self):
        self.cleared.append("reschedule")

    def clear_prepared_cancel(self):
        self.cleared.append("cancel")

    def clear_prepared_message(self):
        self.cleared.append("message")

    def update_voice_state(self, **changes):
        self.updates.append(changes)
        for key, value in changes.items():
            setattr(self.voice_state, key, value)

    def set_resolution(self, *args):
        self.resolutions.append(args)


class FakeTools:
    def __init__(self):
        self.service_calls = 0
        self.hours_calls = 0
        self.services = FakeServiceRepository(self)
        self.hours = FakeHoursRepository(self)


class FakeServiceRepository:
    def __init__(self, parent):
        self.parent = parent

    async def list(self):
        self.parent.service_calls += 1
        return [SimpleNamespace(name="Women's Cut", price=6500, is_active=True)]


class FakeHoursRepository:
    def __init__(self, parent):
        self.parent = parent

    async def list(self):
        self.parent.hours_calls += 1
        return []


async def _run(text, *, session=None, tools=None):
    session = session or FakeCallSession()
    tools = tools or FakeTools()
    spoken = []

    async def speak(message, _direction):
        spoken.append(message)

    handled = await handle_common_enquiry(
        text,
        None,
        call_session=session,
        tools=tools,
        configured_service_names=("Women's Cut", "Men's Cut"),
        tenant=SimpleNamespace(
            currency="NZD", prices_include_gst=True, timezone="Pacific/Auckland"
        ),
        speak=speak,
    )
    return handled, spoken, session, tools


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utterance",
    [
        "How much is a women's cut, and book me in Friday?",
        "How much is a women's cut? Please cancel my appointment.",
        "What's the price of a women's cut? I need to reschedule my appointment.",
        "How much does a men's cut cost, and leave a message for the salon.",
    ],
)
async def test_mixed_price_and_action_turn_falls_through_without_price_side_effects(utterance):
    handled, spoken, session, tools = await _run(utterance)

    assert handled is False
    assert spoken == []
    assert session.cleared == []
    assert session.updates == []
    assert tools.service_calls == 0
    assert tools.hours_calls == 0


@pytest.mark.asyncio
async def test_pure_price_question_uses_actual_common_enquiry_handler():
    handled, spoken, session, tools = await _run("How much is a women's cut?")

    assert handled is True
    assert spoken == ["The Women's Cut is 65 dollars."]
    assert session.voice_state.intent == "enquiry"
    assert session.voice_state.last_tool == "get_salon_information"
    assert session.cleared == ["booking", "reschedule", "cancel", "message"]
    assert session.resolutions
    assert tools.service_calls == 1


@pytest.mark.asyncio
async def test_price_clarification_without_service_asks_which_service():
    handled, spoken, session, tools = await _run("How much does it cost?")

    assert handled is True
    assert spoken == ["Which service would you like the price for?"]
    assert session.voice_state.intent == "enquiry"
    assert tools.service_calls == 0


@pytest.mark.asyncio
async def test_cancellation_policy_question_is_not_mistaken_for_action():
    handled, spoken, _session, tools = await _run("What is your cancellation policy?")

    assert handled is False
    assert spoken == []
    assert tools.service_calls == 0
    assert tools.hours_calls == 0
