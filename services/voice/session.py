from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from uuid import UUID

from sqlalchemy import select

from database.models import Call, CallSummary, TranscriptMessage
from database.session import DBSession
from database.utils import utcnow


@dataclass
class CallResolution:
    outcome: str
    summary_text: str
    follow_up_required: bool = False
    follow_up_notes: str | None = None


@dataclass
class VoiceConversationState:
    """Compact structured state that survives aggressive LLM history trimming."""

    intent: str = "unknown"
    service: str | None = None
    staff: str | None = None
    caller_time_phrase: str | None = None
    starts_at: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    appointment_id: str | None = None
    confirmed: bool = False
    last_tool: str | None = None
    last_tool_ok: bool | None = None
    management_phone_unavailable: bool = False
    message_target: str | None = None
    message_text: str | None = None
    message_callback_phone: str | None = None
    message_customer_name: str | None = None

    def summary(self) -> str:
        def value(item: object | None) -> str:
            if item is None or item == "":
                return "missing"
            return str(item).replace("\n", " ")[:120]

        return (
            f"intent={value(self.intent)}; service={value(self.service)}; staff={value(self.staff)}; "
            f"time_phrase={value(self.caller_time_phrase)}; starts_at={value(self.starts_at)}; "
            f"name={value(self.customer_name)}; phone={value(self.customer_phone)}; "
            f"appointment_id={value(self.appointment_id)}; confirmed={str(self.confirmed).lower()}; "
            f"last_tool={value(self.last_tool)}; last_tool_ok={value(self.last_tool_ok)}; "
            f"management_phone_unavailable={str(self.management_phone_unavailable).lower()}; "
            f"message_target={value(self.message_target)}; "
            f"message_text={'set' if self.message_text else 'missing'}; "
            f"message_callback_phone={value(self.message_callback_phone)}; "
            f"message_customer_name={value(self.message_customer_name)}. "
            "This is memory only; tool results remain authoritative."
        )


class LiveCallSession:
    """Persist a live voice session without coupling provider code to database models."""

    def __init__(
        self,
        db: DBSession,
        *,
        tenant_id: UUID,
        external_call_id: str,
        caller_number: str | None,
        called_number: str | None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.external_call_id = external_call_id
        self.caller_number = caller_number
        self.called_number = called_number
        self.call: Call | None = None
        self._sequence = 0
        self._finished = False
        self._resolution: CallResolution | None = None
        self._last_transcript: tuple[str, str] | None = None
        self._caller_transcripts: list[str] = []
        self.voice_state = VoiceConversationState()
        self._prepared_booking_fingerprint: str | None = None
        self._prepared_booking_payload: dict[str, object] | None = None
        self._booking_phone_digits: str = ""
        self._booking_phone_capture_active = False
        self._booking_name_capture_active = False
        self._booking_spelled_name: str | None = None
        self._prepared_reschedule_fingerprint: str | None = None
        self._prepared_cancel_fingerprint: str | None = None
        self._rejected_appointment_ids: set[str] = set()
        self._verified_management_phone: str | None = None
        self._message_phone_digits: str = ""
        self._prepared_message_fingerprint: str | None = None
        self._prepared_message_payload: dict[str, object] | None = None

    @property
    def call_id(self) -> UUID | None:
        return self.call.id if self.call else None

    @property
    def resolution(self) -> CallResolution | None:
        return self._resolution

    @property
    def recent_caller_transcripts(self) -> tuple[str, ...]:
        """Recent finalized caller turns for bounded tool-side context recovery."""
        return tuple(self._caller_transcripts[-8:])

    @property
    def voice_state_summary(self) -> str:
        return self.voice_state.summary()

    @property
    def latest_caller_transcript(self) -> str | None:
        return self._caller_transcripts[-1] if self._caller_transcripts else None

    def prepare_booking(self, fingerprint: str, payload: dict[str, object] | None = None) -> None:
        self._prepared_booking_fingerprint = fingerprint
        self._prepared_booking_payload = dict(payload) if payload is not None else None

    @property
    def prepared_booking_payload(self) -> dict[str, object] | None:
        return (
            dict(self._prepared_booking_payload)
            if self._prepared_booking_payload is not None
            else None
        )

    @property
    def has_prepared_booking(self) -> bool:
        return bool(self._prepared_booking_fingerprint and self._prepared_booking_payload)

    def booking_is_prepared(self, fingerprint: str) -> bool:
        return bool(fingerprint and fingerprint == self._prepared_booking_fingerprint)

    def clear_prepared_booking(self) -> None:
        self._prepared_booking_fingerprint = None
        self._prepared_booking_payload = None

    @property
    def booking_phone_digits(self) -> str | None:
        return self._booking_phone_digits or None

    @property
    def booking_phone_capture_active(self) -> bool:
        return self._booking_phone_capture_active

    def set_booking_phone_capture(self, active: bool) -> None:
        self._booking_phone_capture_active = bool(active)

    @property
    def booking_name_capture_active(self) -> bool:
        return self._booking_name_capture_active

    def set_booking_name_capture(self, active: bool) -> None:
        self._booking_name_capture_active = bool(active)

    def ingest_booking_phone_fragment(self, digits: str | None) -> str | None:
        clean = "".join(ch for ch in str(digits or "") if ch.isdigit())
        if not clean:
            return self.booking_phone_digits
        # A complete-looking number is treated as a fresh attempt and replaces stale
        # fragments. Short fragments only extend an incomplete number.
        if len(clean) >= 9:
            self._booking_phone_digits = clean[:12]
        elif (
            len(self._booking_phone_digits) < 9
            and len(self._booking_phone_digits) + len(clean) <= 12
        ):
            self._booking_phone_digits += clean
        self.clear_prepared_booking()
        return self.booking_phone_digits

    def clear_booking_phone(self) -> None:
        self._booking_phone_digits = ""
        self.clear_prepared_booking()

    @property
    def booking_spelled_name(self) -> str | None:
        return self._booking_spelled_name

    def set_booking_spelled_name(self, name: str | None) -> None:
        value = " ".join(str(name or "").split()).strip()
        if value:
            self._booking_spelled_name = value
            self.voice_state.customer_name = value
            self.clear_prepared_booking()

    def prepare_reschedule(self, fingerprint: str) -> None:
        self._prepared_reschedule_fingerprint = fingerprint

    def reschedule_is_prepared(self, fingerprint: str) -> bool:
        return bool(fingerprint and fingerprint == self._prepared_reschedule_fingerprint)

    def clear_prepared_reschedule(self) -> None:
        self._prepared_reschedule_fingerprint = None

    @property
    def has_prepared_reschedule(self) -> bool:
        return bool(self._prepared_reschedule_fingerprint)

    def prepare_cancel(self, fingerprint: str) -> None:
        self._prepared_cancel_fingerprint = fingerprint

    def cancel_is_prepared(self, fingerprint: str) -> bool:
        return bool(fingerprint and fingerprint == self._prepared_cancel_fingerprint)

    def clear_prepared_cancel(self) -> None:
        self._prepared_cancel_fingerprint = None

    @property
    def has_prepared_cancel(self) -> bool:
        return bool(self._prepared_cancel_fingerprint)

    @property
    def message_phone_digits(self) -> str | None:
        return self._message_phone_digits or None

    def ingest_message_phone_fragment(self, digits: str | None) -> str | None:
        clean = "".join(ch for ch in str(digits or "") if ch.isdigit())
        if not clean:
            return self.message_phone_digits
        if len(clean) >= 9:
            self._message_phone_digits = clean[:12]
        elif (
            len(self._message_phone_digits) < 9
            and len(self._message_phone_digits) + len(clean) <= 12
        ):
            self._message_phone_digits += clean
        self.clear_prepared_message()
        return self.message_phone_digits

    def clear_message_phone(self) -> None:
        self._message_phone_digits = ""
        self.voice_state.message_callback_phone = None
        self.clear_prepared_message()

    def prepare_message(self, fingerprint: str, payload: dict[str, object]) -> None:
        self._prepared_message_fingerprint = fingerprint
        self._prepared_message_payload = dict(payload)

    @property
    def prepared_message_payload(self) -> dict[str, object] | None:
        return (
            dict(self._prepared_message_payload)
            if self._prepared_message_payload is not None
            else None
        )

    @property
    def has_prepared_message(self) -> bool:
        return bool(self._prepared_message_fingerprint and self._prepared_message_payload)

    def message_is_prepared(self, fingerprint: str) -> bool:
        return bool(fingerprint and fingerprint == self._prepared_message_fingerprint)

    def clear_prepared_message(self) -> None:
        self._prepared_message_fingerprint = None
        self._prepared_message_payload = None

    def clear_message_flow(self) -> None:
        self.clear_prepared_message()
        self._message_phone_digits = ""
        self.clear_voice_state_fields(
            "message_target",
            "message_text",
            "message_callback_phone",
            "message_customer_name",
        )

    @property
    def verified_management_phone(self) -> str | None:
        """Caller-spoken booking phone verified for appointment management in this call."""
        return self._verified_management_phone

    def verify_management_phone(self, phone: str) -> None:
        digits = "".join(ch for ch in str(phone) if ch.isdigit())
        if digits:
            self._verified_management_phone = digits

    @property
    def rejected_appointment_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._rejected_appointment_ids))

    def reject_appointment(self, appointment_id: str | None) -> None:
        if appointment_id:
            self._rejected_appointment_ids.add(str(appointment_id))

    def restore_appointment(self, appointment_id: str | None) -> None:
        if appointment_id:
            self._rejected_appointment_ids.discard(str(appointment_id))

    def clear_voice_state_fields(self, *field_names: str) -> None:
        for name in field_names:
            if hasattr(self.voice_state, name):
                setattr(self.voice_state, name, None)

    def update_voice_state(self, **changes: object) -> None:
        for key, value in changes.items():
            if hasattr(self.voice_state, key) and value is not None and value != "":
                setattr(self.voice_state, key, value)

    async def start(self) -> Call:
        if self.call is not None:
            return self.call
        now = utcnow()
        self.call = Call(
            tenant_id=self.tenant_id,
            external_call_id=self.external_call_id,
            caller_number=self.caller_number,
            called_number=self.called_number,
            status="in_progress",
            outcome="pending",
            started_at=now,
            answered_at=now,
            ended_at=None,
            duration_seconds=None,
            created_at=now,
        )
        self.db.add(self.call)
        await self.db.commit()
        await self.db.refresh(self.call)
        return self.call

    async def append_transcript(self, speaker: str, text: str) -> None:
        value = " ".join(text.split()).strip()
        if not value or self.call is None or self._finished:
            return
        fingerprint = (speaker, value)
        if fingerprint == self._last_transcript:
            return
        self._last_transcript = fingerprint
        if speaker == "caller":
            self._caller_transcripts.append(value)
            if len(self._caller_transcripts) > 12:
                del self._caller_transcripts[:-12]
        self._sequence += 1
        self.db.add(
            TranscriptMessage(
                tenant_id=self.tenant_id,
                call_id=self.call.id,
                speaker=speaker,
                text=value,
                sequence_number=self._sequence,
                is_final=True,
                created_at=utcnow(),
            )
        )
        await self.db.commit()

    def set_resolution(
        self,
        outcome: str,
        summary_text: str,
        *,
        follow_up_required: bool = False,
        follow_up_notes: str | None = None,
    ) -> None:
        self._resolution = CallResolution(
            outcome=outcome,
            summary_text=summary_text,
            follow_up_required=follow_up_required,
            follow_up_notes=follow_up_notes,
        )

    async def finish(self, *, failure_reason: str | None = None) -> Call | None:
        if self.call is None or self._finished:
            return self.call
        self._finished = True
        ended = utcnow()
        resolution = self._resolution
        if resolution is None:
            resolution = CallResolution(
                outcome="failed_fallback",
                summary_text=failure_reason
                or "Live call ended without a completed salon action; follow-up may be required.",
                follow_up_required=True,
                follow_up_notes="Review the call transcript and follow up with the caller if needed.",
            )
        self.call.status = "completed"
        self.call.outcome = resolution.outcome
        self.call.ended_at = ended
        started_at = self.call.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        self.call.duration_seconds = max(1, int((ended - started_at).total_seconds()))

        existing_result = await self.db.execute(
            select(CallSummary).where(
                CallSummary.call_id == self.call.id,
                CallSummary.tenant_id == self.tenant_id,
            )
        )
        summary = existing_result.scalar_one_or_none()
        if summary is None:
            summary = CallSummary(
                tenant_id=self.tenant_id,
                call_id=self.call.id,
                outcome=resolution.outcome,
                summary_text=resolution.summary_text,
                follow_up_required=resolution.follow_up_required,
                follow_up_notes=resolution.follow_up_notes,
                created_at=ended,
                updated_at=ended,
            )
            self.db.add(summary)
        else:
            summary.outcome = resolution.outcome
            summary.summary_text = resolution.summary_text
            summary.follow_up_required = resolution.follow_up_required
            summary.follow_up_notes = resolution.follow_up_notes
            summary.updated_at = ended

        await self.db.commit()
        await self.db.refresh(self.call)
        return self.call
