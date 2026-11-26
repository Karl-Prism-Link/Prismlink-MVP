from __future__ import annotations

from typing import Any

from loguru import logger
from pipecat.frames.frames import FunctionCallResultProperties, TTSSpeakFrame
from pipecat.services.llm_service import FunctionCallParams

from services.voice.speech_cleanup import (
    cancellation_success_speech,
    reschedule_success_speech,
)


def create_voice_tool_handlers(call_session, tools):
    """Build the production Pipecat tool handlers with injectable session/tools."""

    def _tool_args(params: FunctionCallParams, tool_name: str, allowed: set[str]) -> dict[str, Any]:
        args = dict(params.arguments or {})
        unexpected = sorted(set(args) - allowed)
        if unexpected:
            # Never log argument values here; they may contain caller PII.
            logger.warning(
                "PRISM LINK tool={} ignored unexpected argument names={}",
                tool_name,
                unexpected,
            )
        return args

    async def get_salon_information_handler(params: FunctionCallParams):
        args = _tool_args(params, "get_salon_information", {"question"})
        question = str(args.get("question") or "salon information")
        await params.result_callback(await tools.get_salon_information(question))

    async def check_availability_handler(params: FunctionCallParams):
        args = _tool_args(
            params,
            "check_availability",
            {"service_name", "starts_at_local", "staff_name", "caller_time_phrase"},
        )
        selected_appointment_id = call_session.voice_state.appointment_id
        management_mode = call_session.voice_state.intent in {
            "appointment_management",
            "reschedule",
        } and bool(selected_appointment_id)
        if management_mode:
            # A reschedule keeps the existing appointment's service, duration and staff.
            # Ignore any service label/ID the LLM happened to put on the generic
            # availability call; the selected appointment is authoritative.
            call_session.update_voice_state(
                intent="reschedule",
                caller_time_phrase=args.get("caller_time_phrase"),
                confirmed=False,
                last_tool="check_availability",
            )
            result = await tools.check_reschedule_availability(
                str(selected_appointment_id),
                str(args.get("starts_at_local") or ""),
                args.get("caller_time_phrase"),
            )
        else:
            call_session.update_voice_state(
                intent="booking",
                service=args.get("service_name"),
                staff=args.get("staff_name"),
                caller_time_phrase=args.get("caller_time_phrase"),
                confirmed=False,
                last_tool="check_availability",
            )
            result = await tools.check_availability(
                str(args.get("service_name") or ""),
                str(args.get("starts_at_local") or ""),
                args.get("staff_name"),
                args.get("caller_time_phrase"),
            )
        call_session.update_voice_state(
            service=result.get("service"),
            staff=result.get("staff"),
            caller_time_phrase=result.get("caller_time_phrase"),
            starts_at=result.get("starts_at"),
            appointment_id=result.get("appointment_id"),
            last_tool_ok=result.get("ok"),
        )
        await params.result_callback(result)

    async def book_appointment_handler(params: FunctionCallParams):
        args = _tool_args(
            params,
            "book_appointment",
            {
                "service_name",
                "starts_at_local",
                "customer_name",
                "customer_phone",
                "confirmed",
                "staff_name",
                "caller_time_phrase",
            },
        )
        effective_name = (
            call_session.booking_spelled_name or str(args.get("customer_name") or "").strip()
        )
        effective_phone = call_session.booking_phone_digits or str(args.get("customer_phone") or "")
        call_session.update_voice_state(
            intent="booking",
            service=args.get("service_name"),
            staff=args.get("staff_name"),
            caller_time_phrase=args.get("caller_time_phrase"),
            customer_name=effective_name,
            customer_phone=effective_phone,
            confirmed=args.get("confirmed") is True,
            last_tool="book_appointment",
        )
        result = await tools.book_appointment(
            str(args.get("service_name") or ""),
            str(args.get("starts_at_local") or ""),
            effective_name,
            effective_phone,
            args.get("confirmed") is True,
            args.get("staff_name"),
            args.get("caller_time_phrase"),
        )
        call_session.update_voice_state(
            service=result.get("service"),
            staff=result.get("staff"),
            starts_at=result.get("starts_at"),
            appointment_id=result.get("appointment_id"),
            customer_name=result.get("customer_name"),
            customer_phone=result.get("customer_phone"),
            confirmed=result.get("booked") is True,
            last_tool_ok=result.get("ok"),
        )
        await params.result_callback(result)

    async def find_appointments_handler(params: FunctionCallParams):
        args = _tool_args(
            params,
            "find_appointments",
            {"customer_phone", "customer_name", "caller_time_phrase"},
        )
        management_intent = call_session.voice_state.intent
        if management_intent not in {"cancel", "reschedule"}:
            management_intent = "appointment_management"
        call_session.update_voice_state(
            intent=management_intent,
            customer_phone=args.get("customer_phone"),
            customer_name=args.get("customer_name"),
            caller_time_phrase=args.get("caller_time_phrase"),
            confirmed=False,
            last_tool="find_appointments",
        )
        result = await tools.find_appointments(
            str(args.get("customer_phone") or ""),
            args.get("customer_name"),
            args.get("caller_time_phrase"),
        )
        appointments = result.get("appointments") or []
        selected = appointments[0] if len(appointments) == 1 else None
        call_session.update_voice_state(
            appointment_id=selected.get("appointment_id") if selected else None,
            service=selected.get("service") if selected else None,
            starts_at=selected.get("starts_at") if selected else None,
            customer_name=(
                call_session.voice_state.customer_name
                or (selected.get("customer_name") if selected else None)
            ),
            customer_phone=result.get("verified_customer_phone"),
            last_tool_ok=result.get("ok"),
        )
        # For a cancellation with exactly one verified appointment, prepare the mutation
        # before the assistant speaks. This makes the next spoken question the single
        # consequential confirmation instead of requiring two successive "yes" answers.
        if (
            result.get("ok") is True
            and result.get("identity_verified") is True
            and selected is not None
            and call_session.voice_state.intent == "cancel"
        ):
            prepared = await tools.cancel_appointment(
                str(selected.get("appointment_id") or ""), False
            )
            call_session.update_voice_state(
                appointment_id=prepared.get("appointment_id"),
                service=prepared.get("service"),
                starts_at=prepared.get("starts_at"),
                last_tool="cancel_appointment",
                last_tool_ok=prepared.get("ok"),
                confirmed=False,
            )
            await params.result_callback(prepared)
            return
        await params.result_callback(result)

    async def reschedule_appointment_handler(params: FunctionCallParams):
        args = _tool_args(
            params,
            "reschedule_appointment",
            {"appointment_id", "starts_at_local", "confirmed", "caller_time_phrase"},
        )
        call_session.update_voice_state(
            intent="reschedule",
            appointment_id=args.get("appointment_id"),
            caller_time_phrase=args.get("caller_time_phrase"),
            confirmed=args.get("confirmed") is True,
            last_tool="reschedule_appointment",
        )
        result = await tools.reschedule_appointment(
            str(args.get("appointment_id") or ""),
            str(args.get("starts_at_local") or ""),
            args.get("confirmed") is True,
            args.get("caller_time_phrase"),
        )
        call_session.update_voice_state(
            service=result.get("service"),
            starts_at=result.get("starts_at"),
            appointment_id=result.get("appointment_id"),
            confirmed=result.get("rescheduled") is True,
            last_tool_ok=result.get("ok"),
        )
        if result.get("ok") is True and result.get("rescheduled") is True:
            # The tool result is already authoritative. Do not pay for another LLM
            # round-trip just to paraphrase a successful mutation. Store the result in
            # context without running the LLM, then speak the deterministic sentence.
            await params.result_callback(
                result,
                properties=FunctionCallResultProperties(run_llm=False),
            )
            spoken = reschedule_success_speech(result)
            await call_session.append_transcript("assistant", spoken)
            await params.llm.push_frame(TTSSpeakFrame(text=spoken, append_to_context=True))
            return
        await params.result_callback(result)

    async def cancel_appointment_handler(params: FunctionCallParams):
        args = _tool_args(params, "cancel_appointment", {"appointment_id", "confirmed"})
        call_session.update_voice_state(
            intent="cancel",
            appointment_id=args.get("appointment_id"),
            confirmed=args.get("confirmed") is True,
            last_tool="cancel_appointment",
        )
        result = await tools.cancel_appointment(
            str(args.get("appointment_id") or ""),
            args.get("confirmed") is True,
        )
        call_session.update_voice_state(
            service=result.get("service"),
            starts_at=result.get("starts_at"),
            appointment_id=result.get("appointment_id"),
            confirmed=result.get("cancelled") is True and not result.get("already_cancelled"),
            last_tool_ok=result.get("ok"),
        )
        if (
            result.get("ok") is True
            and result.get("cancelled") is True
            and not result.get("already_cancelled")
        ):
            await params.result_callback(
                result,
                properties=FunctionCallResultProperties(run_llm=False),
            )
            spoken = cancellation_success_speech(result)
            await call_session.append_transcript("assistant", spoken)
            await params.llm.push_frame(TTSSpeakFrame(text=spoken, append_to_context=True))
            return
        await params.result_callback(result)

    async def take_message_handler(params: FunctionCallParams):
        args = _tool_args(
            params,
            "take_message",
            {"message", "customer_phone", "customer_name", "confirmed", "target_staff"},
        )
        call_session.update_voice_state(
            intent="message",
            message_text=args.get("message"),
            message_callback_phone=args.get("customer_phone"),
            message_target=args.get("target_staff"),
            message_customer_name=args.get("customer_name"),
            last_tool="take_message",
        )
        result = await tools.take_message(
            str(args.get("message") or ""),
            str(args.get("customer_phone") or ""),
            args.get("customer_name"),
            args.get("confirmed") is True,
            args.get("target_staff"),
        )
        call_session.update_voice_state(
            message_text=result.get("message"),
            message_callback_phone=result.get("customer_phone"),
            message_customer_name=result.get("customer_name"),
            message_target=result.get("target_staff"),
            confirmed=result.get("submitted") is True,
            last_tool_ok=result.get("ok"),
        )
        if result.get("ok") is True and result.get("submitted") is True:
            await params.result_callback(
                result,
                properties=FunctionCallResultProperties(run_llm=False),
            )
            target = str(result.get("target_staff") or "").strip()
            spoken = (
                f"I've left your message for {target}."
                if target
                else "I've left your message for the salon."
            )
            await call_session.append_transcript("assistant", spoken)
            await params.llm.push_frame(TTSSpeakFrame(text=spoken, append_to_context=True))
            return
        await params.result_callback(result)

    return {
        "get_salon_information": get_salon_information_handler,
        "check_availability": check_availability_handler,
        "book_appointment": book_appointment_handler,
        "find_appointments": find_appointments_handler,
        "reschedule_appointment": reschedule_appointment_handler,
        "cancel_appointment": cancel_appointment_handler,
        "take_message": take_message_handler,
    }
