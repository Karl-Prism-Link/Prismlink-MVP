from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from services.voice.speech_cleanup import (
    booking_success_speech,
    cancellation_success_speech,
    mutation_failure_speech,
    reschedule_success_speech,
)


async def execute_confirmation_action(
    action: str | None,
    *,
    call_session: Any,
    tools: Any,
    speak: Callable[[str, Any], Awaitable[None]],
    direction: Any,
) -> bool:
    """Execute or reject one already-classified deterministic confirmation action."""
    if action is None:
        return False
    if action == "booking_confirm":
        result = await tools.book_appointment("", "", "", "", True, None, None)
        call_session.update_voice_state(
            appointment_id=result.get("appointment_id"),
            service=result.get("service"),
            starts_at=result.get("starts_at"),
            customer_name=result.get("customer_name"),
            customer_phone=result.get("customer_phone"),
            confirmed=result.get("booked") is True,
            last_tool="book_appointment",
            last_tool_ok=result.get("ok"),
        )
        if result.get("ok") is True and result.get("booked") is True:
            await speak(booking_success_speech(result), direction)
        else:
            await speak(mutation_failure_speech(result, action="book"), direction)
        return True

    if action == "reschedule_confirm":
        state = call_session.voice_state
        result = await tools.reschedule_appointment(
            str(state.appointment_id or ""),
            str(state.starts_at or ""),
            True,
            state.caller_time_phrase,
        )
        call_session.update_voice_state(
            service=result.get("service"),
            starts_at=result.get("starts_at"),
            appointment_id=result.get("appointment_id"),
            confirmed=result.get("rescheduled") is True,
            last_tool="reschedule_appointment",
            last_tool_ok=result.get("ok"),
        )
        if result.get("ok") is True and result.get("rescheduled") is True:
            await speak(reschedule_success_speech(result), direction)
        else:
            await speak(mutation_failure_speech(result, action="reschedule"), direction)
        return True

    if action == "cancel_confirm":
        state = call_session.voice_state
        result = await tools.cancel_appointment(str(state.appointment_id or ""), True)
        call_session.update_voice_state(
            service=result.get("service"),
            starts_at=result.get("starts_at"),
            appointment_id=result.get("appointment_id"),
            confirmed=result.get("cancelled") is True,
            last_tool="cancel_appointment",
            last_tool_ok=result.get("ok"),
        )
        if result.get("ok") is True and result.get("cancelled") is True:
            await speak(cancellation_success_speech(result), direction)
        else:
            await speak(mutation_failure_speech(result, action="cancel"), direction)
        return True

    if action == "message_confirm":
        state = call_session.voice_state
        result = await tools.take_message("", "", None, True, state.message_target)
        call_session.update_voice_state(
            confirmed=result.get("submitted") is True,
            last_tool="take_message",
            last_tool_ok=result.get("ok"),
        )
        if result.get("ok") is True and result.get("submitted") is True:
            target = str(result.get("target_staff") or "").strip()
            if target:
                await speak(f"I've left your message for {target}.", direction)
            else:
                await speak("I've left your message for the salon.", direction)
        else:
            await speak(
                "I couldn't safely submit that message. I can try taking it again.",
                direction,
            )
        return True

    if action == "message_reject":
        target = call_session.voice_state.message_target
        call_session.clear_prepared_message()
        call_session.clear_message_phone()
        call_session.clear_voice_state_fields("message_text")
        call_session.update_voice_state(intent="message", message_target=target, confirmed=False)
        target_phrase = f" for {target}" if target else ""
        await speak(f"Okay. What message would you like me to leave{target_phrase}?", direction)
        return True

    if action == "booking_reject":
        call_session.clear_prepared_booking()
        call_session.update_voice_state(confirmed=False)
        await speak("Okay. What would you like to change?", direction)
        return True

    if action == "reschedule_reject":
        call_session.clear_prepared_reschedule()
        call_session.clear_voice_state_fields("caller_time_phrase", "starts_at")
        call_session.update_voice_state(confirmed=False, intent="reschedule")
        await speak("Okay. What time would you prefer instead?", direction)
        return True

    if action == "cancel_reject":
        call_session.clear_prepared_cancel()
        call_session.update_voice_state(confirmed=False, intent="appointment_management")
        await speak("Okay, I won't cancel it. Is there anything else I can help with?", direction)
        return True
    return False
