from __future__ import annotations

import os
import re
from typing import Any
from uuid import uuid4

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    AggregatedTextFrame,
    Frame,
    FunctionCallResultProperties,
    LLMContextFrame,
    TTSSpeakFrame,
)
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    AssistantTurnStoppedMessage,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
    UserTurnStoppedMessage,
)
from pipecat.processors.aggregators.llm_text_processor import LLMTextProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner

from apps.voice.prompt import build_voice_system_prompt
from core.config import get_settings
from database.repositories import TenantRepository
from database.session import DBSession
from monitoring import record_voice_turn_latency
from services.service_names import configured_service_name_from_speech
from services.voice.contact import (
    extract_booking_day_reference,
    extract_booking_name_candidate,
    extract_phone_digit_fragment,
    extract_phone_from_speech,
    extract_spelled_name,
    has_explicit_booking_time,
    is_explicit_affirmation,
    is_explicit_rejection,
    is_management_phone_recovery_statement,
    is_management_phone_unavailable,
    recent_booking_datetime_phrase,
    speak_phone_digits,
)
from services.voice.message_routing import (
    callback_message_from_speech,
    extract_inline_message,
    extract_message_correction,
    is_callback_request,
    is_message_request,
    match_configured_staff_name,
    message_caller_name_from_speech,
    message_target_from_speech,
    requested_person_from_speech,
    routing_fallback_speech,
)
from services.voice.model_router import (
    ModelTier,
    confirmation_fastpath_action,
    route_voice_turn,
)
from services.voice.runtime_control import (
    compact_context_messages,
    is_generic_booking_request,
    is_rate_limit_error,
    select_latest_caller_text,
)
from services.voice.salon_enquiries import (
    business_hours_summary_speech,
    is_business_hours_question,
    is_open_now_question,
    open_now_speech,
)
from services.voice.session import LiveCallSession
from services.voice.speech_cleanup import (
    booking_confirmation_speech,
    booking_success_speech,
    cancellation_success_speech,
    deterministic_post_action_social_reply,
    is_spoken_question,
    limit_spoken_questions,
    mutation_failure_speech,
    reschedule_success_speech,
    spoken_segment_key,
)
from services.voice.stt_context import stt_vocabulary_settings
from services.voice.tools import VoiceToolset

settings = get_settings()

transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


def _required(name: str, value: str | None) -> str:
    if not value:
        raise RuntimeError(f"{name} is required for live voice. Add it to .env and restart.")
    return value


class DuplicateSpeechSuppressor(FrameProcessor):
    """Drop junk, duplicate wording, and extra questions within one caller turn."""

    def __init__(self) -> None:
        super().__init__()
        self._seen: set[str] = set()
        self._question_seen = False

    def reset_turn(self) -> None:
        self._seen.clear()
        self._question_seen = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, (AggregatedTextFrame, TTSSpeakFrame)):
            cleaned = limit_spoken_questions(frame.text)
            if cleaned != frame.text:
                logger.debug("PRISM LINK trimmed extra assistant questions in one text segment")
                frame.text = cleaned
            key = spoken_segment_key(frame.text)
            if not key:
                logger.debug("PRISM LINK suppressed punctuation-only assistant segment")
                return
            if key in self._seen:
                logger.debug("PRISM LINK suppressed duplicate assistant sentence")
                return
            if is_spoken_question(frame.text):
                if self._question_seen:
                    logger.debug("PRISM LINK suppressed extra assistant question in same turn")
                    return
                self._question_seen = True
            self._seen.add(key)
        await self.push_frame(frame, direction)


async def run_bot(
    transport: BaseTransport,
    runner_args: RunnerArguments,
    *,
    tenant_slug: str | None = None,
    external_call_id: str | None = None,
    caller_number: str | None = None,
    called_number: str | None = None,
) -> None:
    """Run one isolated receptionist pipeline.

    Browser/WebRTC development uses the configured tenant and synthetic caller ID.
    Telephony callers may instead provide a called number so the tenant is resolved
    from the salon's configured DID before any audio is processed.
    """
    deepgram_key = _required("DEEPGRAM_API_KEY", settings.deepgram_api_key)

    db = DBSession()
    tenants = TenantRepository(db)
    tenant = await tenants.by_phone_number(called_number) if called_number else None
    if tenant is not None and tenant_slug and tenant.slug != tenant_slug:
        await db.close()
        raise RuntimeError("FreeSWITCH tenant slug does not match the called number.")
    if tenant is None:
        tenant_slug = tenant_slug or _required("VOICE_TENANT_SLUG", settings.voice_tenant_slug)
        tenant = await tenants.by_slug(tenant_slug)
    if tenant is None:
        await db.close()
        raise RuntimeError(
            f"VOICE_TENANT_SLUG={tenant_slug!r} does not match a salon in the database."
        )

    is_telephony_call = external_call_id is not None
    external_call_id = external_call_id or f"webrtc_{uuid4().hex}"
    call_session = LiveCallSession(
        db,
        tenant_id=tenant.id,
        external_call_id=external_call_id,
        caller_number=(
            caller_number
            if is_telephony_call
            else caller_number or settings.voice_test_caller_number
        ),
        called_number=called_number or tenant.phone_number,
    )
    tools = VoiceToolset(db, tenant, call_session)

    # Bias Nova-3 toward the salon's real vocabulary. The STT layer, not the
    # LLM, decides whether audio becomes "men's cut" or an acoustically similar
    # phrase such as "mean cat". Deepgram keyterm prompting is therefore built
    # from the tenant's configured salon name, services, and staff.
    service_rows = [row for row in await tools.services.list() if row.is_active]
    configured_service_names = tuple(row.name for row in service_rows)
    staff_rows = [row for row in await tools.staff.list() if row.is_active]
    configured_staff_names = tuple(row.name for row in staff_rows)
    stt_vocab_mode, stt_keyterms, stt_keywords = stt_vocabulary_settings(
        settings.deepgram_stt_model,
        tenant.name,
        (row.name for row in service_rows),
        (row.name for row in staff_rows),
    )
    if stt_vocab_mode == "keyterm":
        logger.info(
            "PRISM LINK Deepgram STT vocabulary mode=keyterm model={} terms={}",
            settings.deepgram_stt_model,
            len(stt_keyterms or []),
        )
    elif stt_vocab_mode == "keywords":
        logger.info(
            "PRISM LINK Deepgram STT vocabulary mode=keywords model={} terms={}",
            settings.deepgram_stt_model,
            len(stt_keywords or []),
        )
    else:
        logger.info(
            "PRISM LINK Deepgram STT vocabulary prompting disabled model={}",
            settings.deepgram_stt_model,
        )

    stt = DeepgramSTTService(
        api_key=deepgram_key,
        settings=DeepgramSTTService.Settings(
            model=settings.deepgram_stt_model,
            language=settings.deepgram_stt_language,
            punctuate=True,
            smart_format=True,
            interim_results=True,
            keyterm=stt_keyterms,
            keywords=stt_keywords,
        ),
    )
    system_prompt = build_voice_system_prompt(tenant)
    if settings.llm_provider == "groq":
        from pipecat.services.groq.llm import GroqLLMService

        class PrismGroqLLMService(GroqLLMService):
            """Groq service with low-cost GPT-OSS reasoning defaults for voice."""

            def create_client(
                self,
                api_key=None,
                base_url=None,
                organization=None,
                project=None,
                default_headers=None,
                **_kwargs,
            ):
                import httpx
                from openai import AsyncOpenAI, DefaultAsyncHttpxClient

                return AsyncOpenAI(
                    api_key=api_key,
                    base_url=base_url,
                    organization=organization,
                    project=project,
                    default_headers=default_headers,
                    max_retries=max(0, settings.groq_max_retries),
                    timeout=max(2.0, settings.groq_timeout_seconds),
                    http_client=DefaultAsyncHttpxClient(
                        limits=httpx.Limits(
                            max_keepalive_connections=100,
                            max_connections=1000,
                            keepalive_expiry=None,
                        )
                    ),
                )

            def build_chat_completion_params(self, params_from_context):
                params = super().build_chat_completion_params(params_from_context)
                messages = list(params_from_context.get("messages") or [])
                latest_user = next(
                    (
                        str(message.get("content") or "")
                        for message in reversed(messages)
                        if message.get("role") == "user"
                    ),
                    call_session.latest_caller_transcript or "",
                )
                after_tool_result = bool(messages and messages[-1].get("role") == "tool")
                decision = route_voice_turn(
                    intent=call_session.voice_state.intent,
                    service=call_session.voice_state.service,
                    starts_at=call_session.voice_state.starts_at,
                    last_tool=call_session.voice_state.last_tool,
                    last_tool_ok=call_session.voice_state.last_tool_ok,
                    confirmed=call_session.voice_state.confirmed,
                    latest_user_text=latest_user,
                    after_tool_result=after_tool_result,
                )
                model = (
                    settings.groq_fast_model
                    if decision.tier is ModelTier.FAST
                    else settings.groq_reasoning_model
                )
                params["model"] = model
                if not decision.use_tools:
                    params.pop("tools", None)
                    params.pop("tool_choice", None)
                if model.startswith("openai/gpt-oss-"):
                    extra_body = dict(params.get("extra_body") or {})
                    extra_body.update(
                        {
                            "reasoning_effort": (
                                "low"
                                if decision.tier is ModelTier.FAST
                                else settings.groq_reasoning_effort
                            ),
                            "include_reasoning": False,
                        }
                    )
                    params["extra_body"] = extra_body
                logger.info(
                    "PRISM LINK LLM route={} model={} tools={} reason={}",
                    decision.tier.value,
                    model,
                    "full" if decision.use_tools else "none",
                    decision.reason,
                )
                return params

        groq_key = _required("GROQ_API_KEY", settings.groq_api_key)
        llm = PrismGroqLLMService(
            api_key=groq_key,
            settings=PrismGroqLLMService.Settings(
                # The request router replaces this per turn. Keep the fast model here
                # as the service-level fallback/default.
                model=settings.groq_fast_model,
                temperature=0.2,
                max_completion_tokens=max(64, settings.groq_max_completion_tokens),
                system_instruction=system_prompt,
            ),
        )
    elif settings.llm_provider == "openrouter":
        from pipecat.services.openrouter.llm import OpenRouterLLMService

        openrouter_key = _required("OPENROUTER_API_KEY", settings.openrouter_api_key)
        llm = OpenRouterLLMService(
            api_key=openrouter_key,
            settings=OpenRouterLLMService.Settings(
                model=settings.openrouter_model,
                temperature=0.2,
                system_instruction=system_prompt,
            ),
        )
    else:
        raise RuntimeError(f"Unsupported LLM_PROVIDER={settings.llm_provider!r}.")

    logger.info(
        "PRISM LINK voice LLM provider={}",
        settings.llm_provider,
    )
    if settings.llm_provider == "groq":
        if settings.groq_fast_model == "llama-3.1-8b-instant":
            logger.warning(
                "PRISM LINK fast model llama-3.1-8b-instant is scheduled for Groq shutdown on 2026-08-16; "
                "configure GROQ_FAST_MODEL to a supported replacement before then"
            )
        logger.info(
            "PRISM LINK Groq router fast_model={} reasoning_model={} max_completion_tokens={} "
            "retries={} timeout={}s reasoning_effort={} context_turns={}",
            settings.groq_fast_model,
            settings.groq_reasoning_model,
            settings.groq_max_completion_tokens,
            settings.groq_max_retries,
            settings.groq_timeout_seconds,
            settings.groq_reasoning_effort,
            settings.voice_context_turns,
        )
    else:
        logger.info("PRISM LINK OpenRouter model={}", settings.openrouter_model)
    tts = DeepgramTTSService(
        api_key=deepgram_key,
        settings=DeepgramTTSService.Settings(voice=settings.deepgram_tts_voice),
    )

    class DeterministicConfirmationGate(FrameProcessor):
        """Commit/reject an already-prepared action without another LLM round-trip."""

        async def _speak(self, text: str, direction: FrameDirection) -> None:
            await call_session.append_transcript("assistant", text)
            await self.push_frame(TTSSpeakFrame(text=text, append_to_context=True), direction)

        async def _prepare_booking_from_state(self, direction: FrameDirection) -> bool:
            state = call_session.voice_state
            digits = call_session.booking_phone_digits or state.customer_phone or ""
            if not (
                state.service and state.starts_at and state.customer_name and 9 <= len(digits) <= 12
            ):
                return False
            result = await tools.book_appointment(
                str(state.service),
                str(state.starts_at),
                str(state.customer_name),
                str(digits),
                False,
                state.staff,
                state.caller_time_phrase,
            )
            call_session.update_voice_state(
                service=result.get("service"),
                starts_at=result.get("starts_at"),
                customer_name=result.get("customer_name"),
                customer_phone=result.get("customer_phone"),
                confirmed=False,
                last_tool="book_appointment",
                last_tool_ok=result.get("ok"),
            )
            if result.get("ok") is True and result.get("ready_to_confirm") is True:
                call_session.set_booking_phone_capture(False)
                call_session.set_booking_name_capture(False)
                await self._speak(booking_confirmation_speech(result), direction)
                logger.info(
                    "PRISM LINK deterministic booking contact capture prepared confirmation"
                )
                return True
            code = str(result.get("code") or "")
            if code == "PHONE_NOT_CAPTURED":
                call_session.set_booking_phone_capture(True)
                await self._speak(
                    "I didn't catch a complete phone number. Please say it digit by digit.",
                    direction,
                )
                return True
            await self._speak(
                "I couldn't safely prepare that booking yet. Please give me the details again.",
                direction,
            )
            return True

        async def _check_booking_availability_direct(
            self,
            service_name: str,
            datetime_phrase: str,
            direction: FrameDirection,
            *,
            log_label: str,
        ) -> bool:
            """Run a booking availability check from caller words without an LLM turn."""
            state = call_session.voice_state
            result = await tools.check_availability(
                service_name,
                "",
                state.staff,
                datetime_phrase,
            )
            call_session.update_voice_state(
                intent="booking",
                service=result.get("service") or service_name,
                staff=result.get("staff"),
                caller_time_phrase=result.get("caller_time_phrase") or datetime_phrase,
                starts_at=result.get("starts_at"),
                confirmed=False,
                last_tool="check_availability",
                last_tool_ok=result.get("ok"),
            )
            if result.get("ok") is True and result.get("available") is True:
                call_session.set_booking_phone_capture(False)
                call_session.set_booking_name_capture(True)
                spoken_start = str(result.get("spoken_start") or "that time")
                await self._speak(
                    f"Great, {spoken_start} is available. May I have your name, please?",
                    direction,
                )
                logger.info("PRISM LINK deterministic booking availability fastpath={}", log_label)
                return True

            if result.get("ok") is True:
                reason = str(result.get("availability_reason") or "")
                if reason == "outside_business_hours":
                    hours = result.get("business_hours_for_day") or {}
                    day = str(
                        hours.get("day")
                        or extract_booking_day_reference(datetime_phrase)
                        or "that day"
                    )
                    opens = str(hours.get("opens_at") or "")
                    closes = str(hours.get("closes_at") or "")
                    if opens and closes:
                        await self._speak(
                            f"That time is outside our opening hours. We're open {opens} to {closes} on {day}. What other time would you like?",
                            direction,
                        )
                    else:
                        await self._speak(
                            "That time is outside our opening hours. What other time would you like?",
                            direction,
                        )
                else:
                    await self._speak(
                        "That time isn't available. What other time would you like?", direction
                    )
                logger.info(
                    "PRISM LINK deterministic booking availability fastpath={}_unavailable",
                    log_label,
                )
                return True

            code = str(result.get("code") or "")
            logger.warning(
                "PRISM LINK deterministic booking availability failed code={} fastpath={}",
                code or "UNKNOWN",
                log_label,
            )
            if code == "NEED_EXACT_TIME":
                day = extract_booking_day_reference(datetime_phrase) or "that day"
                await self._speak(f"What time on {day} would you like?", direction)
                return True
            if code == "TIME_IN_PAST":
                await self._speak(
                    "That time has already passed. What other day and time would you like?",
                    direction,
                )
                return True
            await self._speak(
                "I couldn't safely check that time. What day and time would you like?", direction
            )
            return True

        async def _prepare_message_from_state(self, direction: FrameDirection) -> bool:
            state = call_session.voice_state
            message_text = " ".join(str(state.message_text or "").split()).strip()
            digits = call_session.message_phone_digits or state.message_callback_phone or ""
            if not message_text or not (9 <= len(digits) <= 12):
                return False
            result = await tools.take_message(
                message_text,
                str(digits),
                state.message_customer_name,
                False,
                state.message_target,
            )
            call_session.update_voice_state(
                intent="message",
                message_text=result.get("message") or message_text,
                message_callback_phone=result.get("customer_phone") or digits,
                message_customer_name=result.get("customer_name") or state.message_customer_name,
                message_target=result.get("target_staff") or state.message_target,
                confirmed=False,
                last_tool="take_message",
                last_tool_ok=result.get("ok"),
            )
            if result.get("ok") is True and result.get("ready_to_confirm") is True:
                target = str(result.get("target_staff") or "").strip()
                target_phrase = f" for {target}" if target else ""
                customer_name = str(result.get("customer_name") or "").strip()
                from_phrase = (
                    f" from {customer_name}" if customer_name and customer_name != "Caller" else ""
                )
                spoken_phone = str(result.get("spoken_phone") or speak_phone_digits(str(digits)))
                await self._speak(
                    f"To confirm, I'll leave this message{target_phrase}{from_phrase}: {result.get('message')}. "
                    f"Callback number {spoken_phone}. Is that correct?",
                    direction,
                )
                logger.info(
                    "PRISM LINK deterministic message prepared target_present={}", bool(target)
                )
                return True
            await self._speak(
                "I couldn't safely prepare that message yet. What number should they call you back on?",
                direction,
            )
            return True

        async def _handle_common_enquiry(self, latest_user: str, direction: FrameDirection) -> bool:
            """Answer configured business-hours questions without an LLM round trip."""
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
            await self._speak(speech, direction)
            logger.info("PRISM LINK deterministic salon hours fastpath kind={}", enquiry_kind)
            return True

        async def _handle_routing_and_message(
            self, latest_user: str, direction: FrameDirection
        ) -> bool:
            state = call_session.voice_state

            async def _apply_message_correction() -> bool:
                """Apply caller corrections and require a fresh exact readback/yes."""

                replacement = extract_message_correction(latest_user)
                corrected_name = message_caller_name_from_speech(latest_user)
                corrected_phone = extract_phone_from_speech(
                    latest_user, min_digits=9, max_digits=12
                )
                if not replacement and not corrected_name and corrected_phone is None:
                    return False

                call_session.clear_prepared_message()
                changes: dict[str, object] = {
                    "intent": "message",
                    "confirmed": False,
                }
                if replacement:
                    changes["message_text"] = replacement
                if corrected_name:
                    changes["message_customer_name"] = corrected_name
                call_session.update_voice_state(**changes)
                if corrected_phone is not None:
                    digits = call_session.ingest_message_phone_fragment(corrected_phone.digits)
                    if digits:
                        call_session.update_voice_state(
                            message_callback_phone=digits, confirmed=False
                        )

                if await self._prepare_message_from_state(direction):
                    logger.info("PRISM LINK deterministic message correction prepared")
                    return True

                corrected_state = call_session.voice_state
                if not corrected_state.message_text:
                    await self._speak("What message would you like me to leave?", direction)
                elif not (
                    call_session.message_phone_digits or corrected_state.message_callback_phone
                ):
                    await self._speak("What number should they call you back on?", direction)
                else:
                    await self._speak(
                        "I couldn't safely prepare that correction yet. Please say it again.",
                        direction,
                    )
                return True

            # A prepared message normally reaches the shared deterministic yes/no gate,
            # but material corrections must invalidate that proposal first.
            if call_session.has_prepared_message:
                if await _apply_message_correction():
                    return True
                return False

            # A caller may correct a message even immediately after it was submitted.
            # The message repository is keyed by call, so a fresh confirmed correction
            # updates that call's message instead of creating a duplicate.
            if (
                state.intent == "message"
                and state.confirmed
                and state.last_tool == "take_message"
                and state.last_tool_ok is True
            ):
                if await _apply_message_correction():
                    return True
                return False

            requested = requested_person_from_speech(latest_user)
            if requested:
                matched = match_configured_staff_name(requested, configured_staff_names)
                call_session.clear_prepared_booking()
                call_session.clear_prepared_reschedule()
                call_session.clear_prepared_cancel()
                call_session.clear_prepared_message()
                call_session.clear_message_phone()
                call_session.clear_voice_state_fields(
                    "message_text", "message_callback_phone", "message_target"
                )
                call_session.update_voice_state(
                    intent="routing",
                    message_target=matched,
                    confirmed=False,
                )
                await self._speak(
                    routing_fallback_speech(
                        matched or requested, matched_staff=matched is not None
                    ),
                    direction,
                )
                logger.info(
                    "PRISM LINK deterministic routing fallback matched_staff={}",
                    matched is not None,
                )
                return True

            callback_request = is_callback_request(latest_user)
            if is_message_request(latest_user) or callback_request:
                target = (
                    message_target_from_speech(latest_user, configured_staff_names)
                    or state.message_target
                )
                caller_name = message_caller_name_from_speech(latest_user)
                inline = extract_inline_message(latest_user)
                if not inline and callback_request:
                    inline = callback_message_from_speech(
                        latest_user, caller_name=caller_name or state.message_customer_name
                    )
                phone = extract_phone_from_speech(latest_user, min_digits=9, max_digits=12)
                call_session.clear_prepared_booking()
                call_session.clear_prepared_reschedule()
                call_session.clear_prepared_cancel()
                call_session.clear_prepared_message()
                call_session.clear_message_phone()
                call_session.clear_voice_state_fields(
                    "message_text", "message_callback_phone", "message_customer_name"
                )
                call_session.update_voice_state(
                    intent="message",
                    message_target=target,
                    message_text=inline,
                    message_customer_name=caller_name,
                    confirmed=False,
                )
                digits = None
                if phone is not None:
                    digits = call_session.ingest_message_phone_fragment(phone.digits)
                    if digits:
                        call_session.update_voice_state(
                            message_callback_phone=digits, confirmed=False
                        )

                if inline and digits and 9 <= len(digits) <= 12:
                    await self._prepare_message_from_state(direction)
                elif inline:
                    await self._speak("What number should they call you back on?", direction)
                else:
                    target_phrase = f" for {target}" if target else ""
                    await self._speak(
                        f"What message would you like me to leave{target_phrase}?", direction
                    )
                logger.info("PRISM LINK deterministic message intent fastpath")
                return True

            if state.intent == "routing":
                if is_explicit_affirmation(latest_user):
                    call_session.update_voice_state(intent="message", confirmed=False)
                    target_phrase = f" for {state.message_target}" if state.message_target else ""
                    await self._speak(
                        f"What message would you like me to leave{target_phrase}?", direction
                    )
                    return True
                if is_explicit_rejection(latest_user):
                    call_session.update_voice_state(intent="unknown", confirmed=False)
                    call_session.clear_voice_state_fields(
                        "message_target", "message_text", "message_callback_phone"
                    )
                    await self._speak("Okay. Is there anything else I can help with?", direction)
                    return True

            if state.intent != "message":
                return False

            # Caller identification/correction while capturing a message is data, not
            # a new staff target. Preserve it separately from message_target.
            caller_name = message_caller_name_from_speech(latest_user)
            if caller_name and not extract_message_correction(latest_user):
                call_session.update_voice_state(message_customer_name=caller_name, confirmed=False)
                if state.message_text and (
                    call_session.message_phone_digits or state.message_callback_phone
                ):
                    return await self._prepare_message_from_state(direction)
                if state.message_text:
                    await self._speak("What number should they call you back on?", direction)
                else:
                    await self._speak("What message would you like me to leave?", direction)
                return True

            # Once message intent is established, capture the content verbatim before
            # asking for callback digits. This path deliberately avoids Groq.
            if not state.message_text:
                if is_explicit_affirmation(latest_user) or is_explicit_rejection(latest_user):
                    return False
                clean_message = (
                    callback_message_from_speech(
                        latest_user,
                        caller_name=state.message_customer_name,
                    )
                    or " ".join(latest_user.split()).strip()[:500]
                )
                if clean_message:
                    call_session.update_voice_state(message_text=clean_message, confirmed=False)
                    await self._speak("What number should they call you back on?", direction)
                    logger.info("PRISM LINK deterministic message content captured")
                    return True
                return False

            phone = extract_phone_from_speech(latest_user, min_digits=9, max_digits=12)
            if phone is not None:
                digits = call_session.ingest_message_phone_fragment(phone.digits)
            else:
                fragment = extract_phone_digit_fragment(latest_user)
                digits = (
                    call_session.ingest_message_phone_fragment(fragment)
                    if fragment
                    else call_session.message_phone_digits
                )
            if digits and 9 <= len(digits) <= 12:
                call_session.update_voice_state(message_callback_phone=digits, confirmed=False)
                return await self._prepare_message_from_state(direction)
            if digits:
                await self._speak(
                    f"I have {speak_phone_digits(digits)}. Please give me the rest of the callback number.",
                    direction,
                )
            else:
                await self._speak(
                    "I didn't catch a callback number. Please say it digit by digit.", direction
                )
            return True

        async def _handle_booking_partial_capture(
            self, latest_user: str, direction: FrameDirection
        ) -> bool:
            """Resolve short service clarifications and preserved date/time without an LLM call."""
            state = call_session.voice_state
            if state.starts_at or call_session.has_prepared_booking:
                return False

            recent = call_session.recent_caller_transcripts

            # Resolve a configured service reply before deciding whether this is booking
            # context. A caller will often answer the greeting with only a service name
            # (for example, "Means Cut."). Previously that bare service turn escaped to
            # Groq because it contained neither the word "booking" nor an established
            # booking intent. The deterministic receptionist state machine should own
            # that common path.
            service_reply = configured_service_name_from_speech(
                latest_user, configured_service_names
            )
            booking_context = (
                state.intent == "booking"
                or bool(state.service)
                or service_reply is not None
                or any(
                    re.search(r"\b(?:book|booking|appointment)\b", text, re.I)
                    for text in recent[-4:]
                )
            )
            if not booking_context:
                return False

            generic_booking_request = is_generic_booking_request(latest_user)

            # A plain ``Can I book an appointment?`` is already a complete intent.
            # Do not spend an LLM request just to ask the first deterministic intake
            # question, especially after hours when provider degradation must not turn
            # a valid booking request into a generic connection-error fallback.
            if (
                not state.service
                and service_reply is None
                and (state.intent == "booking" or generic_booking_request)
            ):
                call_session.update_voice_state(intent="booking", confirmed=False)
                await self._speak("Sure, what service would you like to book?", direction)
                logger.info("PRISM LINK deterministic generic booking intent fastpath")
                return True

            # Resolve a configured service reply here, not only in the user-turn event.
            # Pipecat event/frame ordering can vary, and this gate must not depend on the
            # state callback winning a race before the LLM context frame arrives.
            if service_reply:
                call_session.update_voice_state(
                    intent="booking", service=service_reply, confirmed=False
                )
                state = call_session.voice_state

                # The caller may have supplied the requested day/time in the previous
                # turn before we clarified Men's Cut vs Women's Cut. Reuse those exact
                # caller words and check availability directly instead of asking them
                # to repeat themselves or spending a reasoning-model request.
                datetime_phrase = recent_booking_datetime_phrase(recent)
                if datetime_phrase:
                    return await self._check_booking_availability_direct(
                        service_reply,
                        datetime_phrase,
                        direction,
                        log_label="service_clarification",
                    )

                await self._speak("What day and time would you like?", direction)
                logger.info("PRISM LINK deterministic booking partial fastpath=ask_day_time")
                return True

            if state.intent != "booking" or not state.service:
                return False
            # Once the service is authoritative, date/time caller words can be checked
            # directly. This removes an unnecessary reasoning-model round trip and keeps
            # provider limits from interrupting the basic booking state machine.
            if has_explicit_booking_time(latest_user):
                datetime_phrase = recent_booking_datetime_phrase((*recent, latest_user))
                if datetime_phrase:
                    return await self._check_booking_availability_direct(
                        str(state.service),
                        datetime_phrase,
                        direction,
                        log_label="service_resolved_datetime",
                    )
                return False
            day = extract_booking_day_reference(latest_user) or extract_booking_day_reference(
                state.caller_time_phrase or ""
            )
            if day:
                call_session.update_voice_state(caller_time_phrase=day, confirmed=False)
                await self._speak(f"What time on {day} would you like?", direction)
                logger.info("PRISM LINK deterministic booking partial fastpath=ask_time")
                return True
            return False

        async def _handle_booking_contact_capture(
            self, latest_user: str, direction: FrameDirection
        ) -> bool:
            state = call_session.voice_state
            if state.intent != "booking" or not state.service or not state.starts_at:
                return False

            # A caller may correct/spell their name while we are asking for the phone
            # or even after the booking readback. Treat explicit spelling/name language
            # as authoritative structured state and re-prepare the exact booking without
            # asking Groq to reconstruct the contact details.
            spelled = extract_spelled_name(latest_user)
            explicit_name = None
            if spelled:
                explicit_name = spelled
            elif re.search(
                r"\b(?:my name is|name is|spell|spelled|spelt|spelling)\b", latest_user, re.I
            ):
                explicit_name = extract_booking_name_candidate(latest_user)
            if explicit_name and (
                state.customer_name
                or call_session.booking_name_capture_active
                or call_session.booking_phone_capture_active
                or call_session.has_prepared_booking
            ):
                if spelled:
                    call_session.set_booking_spelled_name(explicit_name)
                else:
                    call_session.update_voice_state(customer_name=explicit_name, confirmed=False)
                    call_session.clear_prepared_booking()
                call_session.set_booking_name_capture(False)
                digits = call_session.booking_phone_digits or state.customer_phone or ""
                if 9 <= len(digits) <= 12:
                    call_session.update_voice_state(customer_phone=digits, confirmed=False)
                    logger.info(
                        "PRISM LINK deterministic booking name correction name_length={}",
                        len(explicit_name),
                    )
                    return await self._prepare_booking_from_state(direction)
                call_session.set_booking_phone_capture(True)
                await self._speak(f"Thanks, {explicit_name}. What's your phone number?", direction)
                logger.info("PRISM LINK deterministic booking name correction awaiting_phone")
                return True

            if call_session.has_prepared_booking:
                return False

            if not state.customer_name:
                name = extract_spelled_name(latest_user)
                if name is None and call_session.booking_name_capture_active:
                    name = extract_booking_name_candidate(latest_user)
                if name:
                    call_session.set_booking_spelled_name(name) if extract_spelled_name(
                        latest_user
                    ) else call_session.update_voice_state(customer_name=name)
                    call_session.set_booking_name_capture(False)
                    # Accept a complete phone spoken in the same response, but never guess one.
                    phone = extract_phone_from_speech(latest_user, min_digits=9, max_digits=12)
                    if phone is not None:
                        call_session.ingest_booking_phone_fragment(phone.digits)
                        call_session.update_voice_state(
                            customer_phone=phone.digits, confirmed=False
                        )
                        return await self._prepare_booking_from_state(direction)
                    call_session.set_booking_phone_capture(True)
                    await self._speak(f"Thanks, {name}. What's your phone number?", direction)
                    logger.info(
                        "PRISM LINK deterministic booking name capture name_length={}", len(name)
                    )
                    return True
                if call_session.booking_name_capture_active:
                    await self._speak(
                        "I didn't catch your name. Please say your name, or spell it letter by letter.",
                        direction,
                    )
                    return True
                return False

            if not state.customer_phone and not call_session.booking_phone_capture_active:
                call_session.set_booking_name_capture(False)
                call_session.set_booking_phone_capture(True)
                await self._speak(
                    f"Thanks, {state.customer_name}. What's your phone number?", direction
                )
                return True

            if call_session.booking_phone_capture_active:
                # A spelled-name turn has already been consumed above; only actual digits
                # reach the phone buffer.
                phone = extract_phone_from_speech(latest_user, min_digits=9, max_digits=12)
                if phone is not None:
                    digits = call_session.ingest_booking_phone_fragment(phone.digits)
                else:
                    fragment = extract_phone_digit_fragment(latest_user)
                    digits = (
                        call_session.ingest_booking_phone_fragment(fragment)
                        if fragment
                        else call_session.booking_phone_digits
                    )
                if digits and 9 <= len(digits) <= 12:
                    call_session.update_voice_state(customer_phone=digits, confirmed=False)
                    return await self._prepare_booking_from_state(direction)
                if digits:
                    await self._speak(
                        f"I have {speak_phone_digits(digits)}. Please give me the rest of the phone number.",
                        direction,
                    )
                else:
                    await self._speak(
                        "I didn't catch a phone number. Please say it digit by digit.", direction
                    )
                return True

            return False

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            if not isinstance(frame, LLMContextFrame):
                await self.push_frame(frame, direction)
                return

            messages = frame.context.get_messages()
            context_latest_user = next(
                (
                    str(message.get("content") or "")
                    for message in reversed(messages)
                    if message.get("role") == "user"
                ),
                "",
            )
            latest_user = select_latest_caller_text(
                context_latest_user,
                call_session.recent_caller_transcripts,
            )
            # Keep the deterministic guard correct even if Pipecat's event callback and
            # LLMContextFrame arrive in either order. Never append a known older context
            # turn over a newer session transcript.
            if latest_user and latest_user not in call_session.recent_caller_transcripts:
                await call_session.append_transcript("caller", latest_user)

            social_reply = deterministic_post_action_social_reply(latest_user)
            if (
                social_reply
                and call_session.voice_state.confirmed
                and call_session.voice_state.last_tool_ok is True
                and call_session.voice_state.last_tool
                in {
                    "book_appointment",
                    "reschedule_appointment",
                    "cancel_appointment",
                    "take_message",
                }
            ):
                await self._speak(social_reply, direction)
                logger.info("PRISM LINK deterministic post-action social fastpath")
                return

            if await self._handle_common_enquiry(latest_user, direction):
                return
            if await self._handle_routing_and_message(latest_user, direction):
                return
            if await self._handle_booking_partial_capture(latest_user, direction):
                return
            if await self._handle_booking_contact_capture(latest_user, direction):
                return

            action = confirmation_fastpath_action(
                latest_user_text=latest_user,
                has_prepared_booking=call_session.has_prepared_booking,
                has_prepared_reschedule=call_session.has_prepared_reschedule,
                has_prepared_cancel=call_session.has_prepared_cancel,
                has_prepared_message=call_session.has_prepared_message,
                is_affirmation=is_explicit_affirmation(latest_user),
                is_rejection=is_explicit_rejection(latest_user),
            )
            if action is None:
                await self.push_frame(frame, direction)
                return

            logger.info("PRISM LINK deterministic confirmation fastpath action={}", action)

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
                    await self._speak(booking_success_speech(result), direction)
                else:
                    await self._speak(mutation_failure_speech(result, action="book"), direction)
                return

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
                    await self._speak(reschedule_success_speech(result), direction)
                else:
                    await self._speak(
                        mutation_failure_speech(result, action="reschedule"), direction
                    )
                return

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
                    await self._speak(cancellation_success_speech(result), direction)
                else:
                    await self._speak(mutation_failure_speech(result, action="cancel"), direction)
                return

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
                        await self._speak(f"I've left your message for {target}.", direction)
                    else:
                        await self._speak("I've left your message for the salon.", direction)
                else:
                    await self._speak(
                        "I couldn't safely submit that message. I can try taking it again.",
                        direction,
                    )
                return

            if action == "message_reject":
                target = call_session.voice_state.message_target
                call_session.clear_prepared_message()
                call_session.clear_message_phone()
                call_session.clear_voice_state_fields("message_text")
                call_session.update_voice_state(
                    intent="message", message_target=target, confirmed=False
                )
                target_phrase = f" for {target}" if target else ""
                await self._speak(
                    f"Okay. What message would you like me to leave{target_phrase}?", direction
                )
                return

            if action == "booking_reject":
                call_session.clear_prepared_booking()
                call_session.update_voice_state(confirmed=False)
                await self._speak("Okay. What would you like to change?", direction)
                return

            if action == "reschedule_reject":
                call_session.clear_prepared_reschedule()
                call_session.clear_voice_state_fields("caller_time_phrase", "starts_at")
                call_session.update_voice_state(confirmed=False, intent="reschedule")
                await self._speak("Okay. What time would you prefer instead?", direction)
                return

            if action == "cancel_reject":
                call_session.clear_prepared_cancel()
                call_session.update_voice_state(confirmed=False, intent="appointment_management")
                await self._speak(
                    "Okay, I won't cancel it. Is there anything else I can help with?", direction
                )
                return

            await self.push_frame(frame, direction)

    confirmation_gate = DeterministicConfirmationGate()

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

    def _nullable_string_property(description: str | None = None) -> dict[str, Any]:
        """JSON Schema for an optional string that models may explicitly send as null."""
        schema: dict[str, Any] = {
            "anyOf": [
                {"type": "string"},
                {"type": "null"},
            ]
        }
        if description:
            schema["description"] = description
        return schema

    get_salon_information_schema = FunctionSchema(
        name="get_salon_information",
        description="Get configured salon facts.",
        properties={"question": {"type": "string"}},
        required=["question"],
        handler=get_salon_information_handler,
    )

    check_availability_schema = FunctionSchema(
        name="check_availability",
        description="Check a requested booking time. After an appointment is selected for reschedule, this automatically checks the move using the existing appointment service/duration.",
        properties={
            "service_name": {"type": "string"},
            "starts_at_local": _nullable_string_property(
                "Optional salon-local ISO best guess; omit or send null if unknown."
            ),
            "staff_name": _nullable_string_property(
                "Optional staff preference; omit or send null when the caller has no preference."
            ),
            "caller_time_phrase": {
                "type": "string",
                "description": "Caller's date/time words verbatim.",
            },
        },
        required=["service_name", "caller_time_phrase"],
        handler=check_availability_handler,
    )

    book_appointment_schema = FunctionSchema(
        name="book_appointment",
        description="Prepare booking details with confirmed=false, then commit only after explicit final confirmation with confirmed=true.",
        properties={
            "service_name": {"type": "string"},
            "starts_at_local": _nullable_string_property(
                "Optional ISO best guess; omit or send null if unknown."
            ),
            "customer_name": {"type": "string"},
            "customer_phone": {"type": "string"},
            "confirmed": {"type": "boolean"},
            "staff_name": _nullable_string_property(
                "Optional staff preference; omit or send null when the caller has no preference."
            ),
            "caller_time_phrase": {
                "type": "string",
                "description": "Caller's accepted date/time words verbatim.",
            },
        },
        required=[
            "service_name",
            "customer_name",
            "customer_phone",
            "confirmed",
            "caller_time_phrase",
        ],
        handler=book_appointment_handler,
    )

    find_appointments_schema = FunctionSchema(
        name="find_appointments",
        description="Find appointments for safe reschedule/cancel identification.",
        properties={
            "customer_phone": {"type": "string"},
            "customer_name": _nullable_string_property(
                "Optional customer name; omit or send null if not known."
            ),
            "caller_time_phrase": _nullable_string_property(
                "Optional caller words identifying the existing appointment, such as 'on Monday'."
            ),
        },
        required=["customer_phone"],
        handler=find_appointments_handler,
    )

    reschedule_appointment_schema = FunctionSchema(
        name="reschedule_appointment",
        description="Move a selected appointment only after availability precheck/readback and a new explicit confirmation.",
        properties={
            "appointment_id": {"type": "string"},
            "starts_at_local": _nullable_string_property(
                "Optional ISO best guess; omit or send null if unknown."
            ),
            "caller_time_phrase": {
                "type": "string",
                "description": "Caller date/time words verbatim.",
            },
            "confirmed": {"type": "boolean"},
        },
        required=["appointment_id", "caller_time_phrase", "confirmed"],
        handler=reschedule_appointment_handler,
    )

    cancel_appointment_schema = FunctionSchema(
        name="cancel_appointment",
        description=(
            "Prepare or perform cancellation. First call with confirmed=false to get authoritative "
            "appointment details for readback; only after the caller explicitly says yes call again with confirmed=true."
        ),
        properties={
            "appointment_id": {"type": "string"},
            "confirmed": {"type": "boolean"},
        },
        required=["appointment_id", "confirmed"],
        handler=cancel_appointment_handler,
    )

    take_message_schema = FunctionSchema(
        name="take_message",
        description=(
            "Prepare or submit a concise follow-up message. First call with confirmed=false, "
            "read back the exact message and callback number, then only after a fresh explicit yes "
            "call again with confirmed=true."
        ),
        properties={
            "message": {"type": "string"},
            "customer_phone": {"type": "string"},
            "customer_name": _nullable_string_property(
                "Optional customer name; omit or send null if not known."
            ),
            "target_staff": _nullable_string_property(
                "Optional configured staff recipient for the message."
            ),
            "confirmed": {"type": "boolean"},
        },
        required=["message", "customer_phone"],
        handler=take_message_handler,
    )

    context = LLMContext(
        tools=[
            get_salon_information_schema,
            check_availability_schema,
            book_appointment_schema,
            find_appointments_schema,
            reschedule_appointment_schema,
            cancel_appointment_schema,
            take_message_schema,
        ]
    )
    context.add_message(
        {
            "role": "developer",
            "content": (
                f"Caller ID hint: {call_session.caller_number or 'unavailable'}. "
                "Treat it as a lookup hint, not proof of identity."
            ),
        }
    )
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    llm_text_processor = LLMTextProcessor()
    speech_deduper = DuplicateSpeechSuppressor()

    latency_observer = UserBotLatencyObserver()

    @latency_observer.event_handler("on_latency_measured")
    async def on_latency_measured(_observer, latency: float):
        record_voice_turn_latency(latency)
        logger.info("PRISM LINK user-to-bot latency={:.3f}s", latency)

    @latency_observer.event_handler("on_latency_breakdown")
    async def on_latency_breakdown(_observer, breakdown):
        for event in breakdown.chronological_events():
            logger.info("PRISM LINK latency {}", event)

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            confirmation_gate,
            llm,
            llm_text_processor,
            speech_deduper,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            observers=[latency_observer],
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
    )

    rate_limit_fallback_spoken = False

    def refresh_compact_context() -> None:
        compact = compact_context_messages(
            context.get_messages(),
            max_user_turns=settings.voice_context_turns,
            state_summary=call_session.voice_state_summary,
        )
        context.set_messages(compact)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client):
        logger.info("PRISM LINK voice client connected for tenant {}", tenant.slug)
        await call_session.start()
        await call_session.append_transcript("assistant", tenant.greeting)
        await worker.queue_frames([TTSSpeakFrame(text=tenant.greeting, append_to_context=True)])

    @user_aggregator.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(_aggregator, _strategy, message: UserTurnStoppedMessage):
        nonlocal rate_limit_fallback_spoken
        rate_limit_fallback_spoken = False
        speech_deduper.reset_turn()
        if message.content:
            await call_session.append_transcript("caller", message.content)
            lowered = message.content.casefold()
            requested_staff = requested_person_from_speech(message.content)
            if requested_staff:
                matched_staff = match_configured_staff_name(requested_staff, configured_staff_names)
                call_session.clear_prepared_booking()
                call_session.clear_prepared_reschedule()
                call_session.clear_prepared_cancel()
                call_session.clear_prepared_message()
                call_session.clear_voice_state_fields(
                    "message_target", "message_text", "message_callback_phone"
                )
                call_session.update_voice_state(
                    intent="routing",
                    message_target=matched_staff,
                    confirmed=False,
                )
            elif is_message_request(message.content) or is_callback_request(message.content):
                target = (
                    message_target_from_speech(message.content, configured_staff_names)
                    or call_session.voice_state.message_target
                )
                call_session.clear_prepared_booking()
                call_session.clear_prepared_reschedule()
                call_session.clear_prepared_cancel()
                call_session.clear_prepared_message()
                caller_name = message_caller_name_from_speech(message.content)
                call_session.update_voice_state(
                    intent="message",
                    message_target=target,
                    message_customer_name=caller_name,
                    confirmed=False,
                )
            elif "cancel" in lowered:
                call_session.update_voice_state(intent="cancel", confirmed=False)
            elif (
                "resched" in lowered
                or "move my appointment" in lowered
                or "change my appointment" in lowered
            ):
                call_session.update_voice_state(intent="reschedule", confirmed=False)
            elif call_session.voice_state.intent not in {
                "cancel",
                "reschedule",
                "appointment_management",
            } and is_generic_booking_request(message.content):
                call_session.update_voice_state(intent="booking", confirmed=False)

            if call_session.voice_state.intent == "booking":
                service_alias = configured_service_name_from_speech(
                    message.content, configured_service_names
                )
                if service_alias:
                    call_session.update_voice_state(service=service_alias)
                day_reference = extract_booking_day_reference(message.content)
                if day_reference and not has_explicit_booking_time(message.content):
                    call_session.update_voice_state(caller_time_phrase=day_reference)
                # Booking name/phone capture is handled by the deterministic gate immediately
                # before the LLM. Keeping contact collection in one place avoids double-
                # buffering phone fragments and lets spelling/phone turns bypass provider limits.

            if call_session.voice_state.intent in {
                "cancel",
                "reschedule",
                "appointment_management",
            }:
                if is_management_phone_unavailable(message.content):
                    call_session.update_voice_state(
                        management_phone_unavailable=True,
                        confirmed=False,
                    )
                    logger.info(
                        "PRISM LINK caller cannot provide booking phone; management fallback enabled"
                    )
                elif (
                    call_session.voice_state.management_phone_unavailable
                    and is_management_phone_recovery_statement(message.content)
                ):
                    call_session.update_voice_state(management_phone_unavailable=False)
                    logger.info("PRISM LINK caller explicitly recovered booking phone")
                elif (
                    not call_session.voice_state.management_phone_unavailable
                    and extract_phone_from_speech(message.content) is not None
                ):
                    call_session.update_voice_state(management_phone_unavailable=False)
            # A clear "no" immediately after appointment lookup rejects that candidate.
            # Keep that fact outside the LLM so the same appointment is not presented
            # repeatedly while the caller describes a different day/date.
            if (
                is_explicit_rejection(message.content)
                and call_session.voice_state.last_tool == "find_appointments"
                and call_session.voice_state.appointment_id
            ):
                rejected_id = call_session.voice_state.appointment_id
                call_session.reject_appointment(rejected_id)
                call_session.clear_voice_state_fields("appointment_id", "service", "starts_at")
                call_session.update_voice_state(confirmed=False)
                logger.info("PRISM LINK caller rejected appointment candidate")

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(_aggregator, message: AssistantTurnStoppedMessage):
        if message.content:
            await call_session.append_transcript("assistant", message.content)
            if call_session.voice_state.intent == "booking":
                assistant_text = message.content.casefold()
                if (
                    call_session.voice_state.last_tool == "book_appointment"
                    and "is that correct" in assistant_text
                ):
                    call_session.set_booking_phone_capture(False)
                    call_session.set_booking_name_capture(False)
                elif "phone number" in assistant_text and re.search(
                    r"\b(what|give|tell|could|please|need|have|use)\b", assistant_text
                ):
                    call_session.set_booking_name_capture(False)
                    call_session.set_booking_phone_capture(True)
                elif "name" in assistant_text and re.search(
                    r"\b(what|give|tell|could|please|may|have|your)\b", assistant_text
                ):
                    call_session.set_booking_name_capture(True)
        refresh_compact_context()

    def _rate_limit_recovery_speech() -> str:
        """Return a state-aware degraded response instead of asking the caller to loop.

        Provider failure must not erase deterministic booking progress. When PRISM LINK
        already knows the next missing field, ask for that field directly.
        """
        state = call_session.voice_state
        if state.intent == "booking":
            if state.service and not state.starts_at:
                day = extract_booking_day_reference(state.caller_time_phrase or "")
                if day:
                    return f"What time on {day} would you like?"
                return "What day and time would you like?"
            if state.starts_at and not state.customer_name:
                call_session.set_booking_name_capture(True)
                return "May I have your name, please?"
            if state.starts_at and state.customer_name and not state.customer_phone:
                call_session.set_booking_phone_capture(True)
                return f"Thanks, {state.customer_name}. What's your phone number?"
        if state.intent == "routing":
            return routing_fallback_speech(
                state.message_target, matched_staff=bool(state.message_target)
            )
        if state.intent == "message":
            if not state.message_text:
                target_phrase = f" for {state.message_target}" if state.message_target else ""
                return f"What message would you like me to leave{target_phrase}?"
            if not (call_session.message_phone_digits or state.message_callback_phone):
                return "What number should they call you back on?"
            return "I have the message details. Please confirm the readback before I submit it."
        return "I'm having a brief connection issue. I can take a message for the salon instead."

    @worker.event_handler("on_pipeline_error")
    async def on_pipeline_error(_worker, frame):
        nonlocal rate_limit_fallback_spoken
        error_text = str(frame.error)
        if not is_rate_limit_error(error_text) or rate_limit_fallback_spoken:
            return
        rate_limit_fallback_spoken = True
        logger.warning("PRISM LINK Groq rate limit persisted after bounded provider retry")
        recovery = _rate_limit_recovery_speech()
        await call_session.append_transcript("assistant", recovery)
        await worker.queue_frames([TTSSpeakFrame(text=recovery, append_to_context=True)])

    async def finish_and_cancel(reason: str | None = None):
        try:
            await call_session.finish(failure_reason=reason)
        finally:
            await worker.cancel()
            await db.close()

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        logger.info("PRISM LINK voice client disconnected")
        await finish_and_cancel()

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)
    try:
        await runner.run()
    finally:
        if not call_session.resolution and call_session.call_id:
            await call_session.finish(
                failure_reason="Voice runtime ended before a salon action completed."
            )
        try:
            await db.close()
        except Exception:
            pass


async def bot(runner_args: RunnerArguments):
    """Pipecat runner entry point for local WebRTC voice development."""
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    os.environ.setdefault("PIPECAT_LOG_LEVEL", "INFO")
    from pipecat.runner.run import main

    main()
