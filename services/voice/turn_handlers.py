from __future__ import annotations

from loguru import logger

from services.service_names import configured_service_name_from_speech
from services.voice.salon_enquiries import (
    business_hours_summary_speech,
    has_consequential_salon_action,
    is_business_hours_question,
    is_open_now_question,
    is_price_question,
    is_price_service_clarification,
    open_now_speech,
    price_speech,
)


async def handle_common_enquiry(
    latest_user: str,
    direction,
    *,
    call_session,
    tools,
    configured_service_names,
    tenant,
    speak,
) -> bool:
    """Answer configured salon fact questions without an LLM round trip."""
    state = call_session.voice_state
    recent = call_session.recent_caller_transcripts
    service_reply = configured_service_name_from_speech(latest_user, configured_service_names)
    price_service_clarification = bool(
        service_reply
        and is_price_service_clarification(
            latest_user,
            recent,
            intent=state.intent,
            last_tool=state.last_tool,
        )
    )
    has_price_intent = is_price_question(latest_user) or price_service_clarification
    has_other_action = has_consequential_salon_action(latest_user)
    if has_price_intent and not has_other_action:
        # Treat pricing as a separate enquiry before branching on whether a
        # configured price exists. Never leave a prepared action armed, and
        # never record a message request until a message is actually submitted.
        call_session.clear_prepared_booking()
        call_session.clear_prepared_reschedule()
        call_session.clear_prepared_cancel()
        call_session.clear_prepared_message()
        call_session.update_voice_state(
            intent="enquiry",
            confirmed=False,
            last_tool="get_salon_information",
            last_tool_ok=None,
        )
        service_name = service_reply
        if service_name is None:
            await speak("Which service would you like the price for?", direction)
            call_session.update_voice_state(
                intent="enquiry", confirmed=False, last_tool="get_salon_information"
            )
            return True

        service_rows = [row for row in await tools.services.list() if row.is_active]
        service = next(
            (row for row in service_rows if row.name == service_name),
            None,
        )
        if service is None or service.price is None:
            call_session.set_resolution(
                "enquiry_resolved",
                f"Price is not configured for {service_name}; caller was offered a message, but no message was taken.",
            )
            await speak(
                f"I don't have a price configured for {service_name}. I can take a message for the salon if you'd like.",
                direction,
            )
            return True

        speech = price_speech(
            service_name,
            service.price,
            currency=str(getattr(tenant, "currency", "NZD") or "NZD"),
            prices_include_gst=bool(getattr(tenant, "prices_include_gst", True)),
        )
        call_session.update_voice_state(
            intent="enquiry",
            confirmed=False,
            last_tool="get_salon_information",
            last_tool_ok=True,
        )
        call_session.set_resolution(
            "enquiry_resolved",
            f"Answered configured salon price enquiry for {service_name}.",
        )
        await speak(speech, direction)
        logger.info("PRISM LINK deterministic salon price fastpath service={}", service_name)
        return True

    if not is_business_hours_question(latest_user):
        return False

    hours = await tools.hours.list()
    if is_open_now_question(latest_user):
        speech = open_now_speech(hours, tenant.timezone)
        enquiry_kind = "open_now"
    else:
        speech = business_hours_summary_speech(hours)
        enquiry_kind = "business_hours"

    call_session.clear_prepared_booking()
    call_session.clear_prepared_reschedule()
    call_session.clear_prepared_cancel()
    call_session.clear_prepared_message()
    call_session.update_voice_state(
        intent="enquiry",
        confirmed=False,
        last_tool="get_salon_information",
        last_tool_ok=True,
    )
    call_session.set_resolution(
        "enquiry_resolved",
        f"Answered configured salon {enquiry_kind} enquiry.",
    )
    await speak(speech, direction)
    logger.info("PRISM LINK deterministic salon hours fastpath kind={}", enquiry_kind)
    return True
