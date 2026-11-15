from __future__ import annotations

import os
import re
from typing import Any
from uuid import uuid4

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
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
    flatten_tool_result_messages,
    route_voice_turn,
)
from services.voice.runtime_control import (
    RollingModelTokenBudget,
    compact_context_messages,
    is_generic_booking_request,
    is_rate_limit_error,
    select_latest_caller_text,
)
from services.voice.safety import is_health_only_request, is_immediate_emergency_language
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
    is_farewell_utterance,
    is_spoken_question,
    limit_spoken_questions,
    mutation_failure_speech,
    reschedule_success_speech,
    spoken_segment_key,
)
from services.voice.stt_context import stt_vocabulary_settings
from services.voice.tools import VoiceToolset

settings = get_settings()

# One API process hosts concurrent Pipecat calls. Share the budget across those
# calls, independently for each Groq model, and keep 1,000 TPM below Groq's 8,000 cap.
_groq_token_budget = RollingModelTokenBudget(
    tokens_per_minute=7000,
    window_seconds=60.0,
    max_wait_seconds=1.5,
)

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
        raise RuntimeError("Configured tenant slug does not match the called number.")
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
        encoding="linear16",
        sample_rate=8000,
        channels=1,
        # A 300 ms endpoint split callers mid-sentence during natural pauses.
        # 650 ms trades a small amount of latency for more complete caller turns.
        endpointing=650,
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
            """Groq service with per-model TPM admission control for live voice."""

            async def get_chat_completions(self, context):
                adapter = self.get_llm_adapter()
                invocation_params = adapter.get_llm_invocation_params(
                    context,
                    system_instruction=self._settings.system_instruction,
                    convert_developer_to_user=not self.supports_developer_role,
                )
                params = self.build_chat_completion_params(invocation_params)
                model = str(params.get("model") or self._settings.model)
                estimate = _groq_token_budget.estimate_request_tokens(params)
                wait_seconds = await _groq_token_budget.reserve(model, estimate)
                logger.info(
                    "PRISM LINK Groq TPM admission model={} estimated_tokens={} wait_ms={}",
                    model,
                    estimate,
                    round(wait_seconds * 1000),
                )
                # Reuse Pipecat's provider implementation for timeout and SDK retry
                # handling after the request has been admitted.
                return await super().get_chat_completions(context)

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
                if after_tool_result and not decision.use_tools:
                    params["messages"] = flatten_tool_result_messages(messages)
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
                call_session.mark_message_readback_completed()
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
 