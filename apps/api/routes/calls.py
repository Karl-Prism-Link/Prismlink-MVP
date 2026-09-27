from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException

from apps.api.dependencies import AuthContext, get_auth_context
from apps.api.schemas import (
    AppointmentCreate,
    AppointmentMove,
    CallOut,
    CallSimulateIn,
    CallSummaryOut,
    SimulatedCallOut,
)
from apps.voice.runtime import SalonIntentClassifier
from database.models import Call, CallSummary, SalonMessage, Tenant, TranscriptMessage
from database.repositories import BusinessHoursRepository, CallRepository, ServiceRepository
from database.session import DBSession, get_db
from database.utils import utcnow
from services.booking import BookingService

router = APIRouter(prefix="/api/v1/calls", tags=["calls"])
classifier = SalonIntentClassifier()


def to_call_out(call: Call, summary: CallSummary | None) -> CallOut:
    return CallOut(
        id=call.id,
        external_call_id=call.external_call_id,
        caller_number=call.caller_number,
        called_number=call.called_number,
        status=call.status,
        outcome=call.outcome,
        started_at=call.started_at,
        ended_at=call.ended_at,
        duration_seconds=call.duration_seconds,
        summary=(
            CallSummaryOut(
                outcome=summary.outcome,
                summary_text=summary.summary_text,
                follow_up_required=summary.follow_up_required,
                follow_up_notes=summary.follow_up_notes,
            )
            if summary
            else None
        ),
    )


@router.get("", response_model=list[CallOut])
async def list_calls(
    auth: AuthContext = Depends(get_auth_context), db: DBSession = Depends(get_db)
) -> list[CallOut]:
    repo = CallRepository(db, auth.tenant_id)
    rows = await repo.list()
    result: list[CallOut] = []
    for row in rows:
        result.append(to_call_out(row, await repo.summary(row.id)))
    return result


@router.get("/{call_id}", response_model=CallOut)
async def get_call(
    call_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: DBSession = Depends(get_db),
) -> CallOut:
    repo = CallRepository(db, auth.tenant_id)
    row = await repo.get(call_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Call not found")
    return to_call_out(row, await repo.summary(call_id))


@router.post("/simulate", response_model=SimulatedCallOut)
async def simulate_call(
    payload: CallSimulateIn,
    auth: AuthContext = Depends(get_auth_context),
    db: DBSession = Depends(get_db),
) -> SimulatedCallOut:
    """Exercise the salon call decision tree without SIP/STT/TTS credentials."""
    tenant = await db.get(Tenant, auth.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Salon not found")

    started = utcnow()
    call = Call(
        tenant_id=auth.tenant_id,
        external_call_id=f"sim_{uuid4().hex}",
        caller_number=payload.caller_number,
        called_number=tenant.phone_number,
        status="in_progress",
        outcome="pending",
        started_at=started,
        answered_at=started,
        ended_at=None,
        duration_seconds=None,
        created_at=started,
    )
    db.add(call)
    await db.flush()
    db.add(
        TranscriptMessage(
            tenant_id=auth.tenant_id,
            call_id=call.id,
            speaker="caller",
            text=payload.utterance,
            sequence_number=1,
            is_final=True,
            created_at=started,
        )
    )

    intent = classifier.classify(payload.utterance)
    outcome = "failed_fallback"
    reply = "I can take a message and have the salon follow up with you."
    summary_text = f"Caller request was unclear: {payload.utterance[:240]}"
    follow_up = True
    follow_up_notes: str | None = "Salon follow-up required."
    appointment = None
    booking = BookingService(db, auth.tenant_id, auth.user_id)

    if intent.intent == "booking":
        required = [
            payload.service_id,
            payload.starts_at,
            payload.customer_name,
            payload.customer_phone,
        ]
        if all(required) and payload.confirmed:
            appt_payload = AppointmentCreate(
                customer_name=payload.customer_name or "Caller",
                customer_phone=payload.customer_phone or payload.caller_number or "unknown",
                service_id=payload.service_id,
                staff_member_id=payload.staff_member_id,
                starts_at=payload.starts_at,
                idempotency_key=f"call-{call.id}",
                confirmed=True,
                source="voice",
                notes="Created from simulated call flow",
            )
            try:
                appointment = await booking.create(appt_payload, call_id=call.id)
                outcome = "booked"
                reply = "Your appointment is confirmed."
                summary_text = (
                    f"Booked {appointment.customer_name} for {appointment.starts_at.isoformat()}."
                )
                follow_up = False
                follow_up_notes = None
            except HTTPException as exc:
                outcome = "message_taken"
                reply = (
                    "I can't safely confirm that time right now. I'll record this for follow-up."
                )
                summary_text = f"Booking could not be completed: {exc.detail}"
        else:
            outcome = "message_taken"
            reply = (
                "I can help book that. I need the service, preferred time, your name and phone number, "
                "and I will confirm the details before making the booking."
            )
            summary_text = "Booking enquiry needs additional details or explicit confirmation."
    elif intent.intent == "reschedule":
        if payload.appointment_id and payload.starts_at and payload.confirmed:
            try:
                appointment = await booking.reschedule(
                    payload.appointment_id,
                    AppointmentMove(
                        starts_at=payload.starts_at,
                        idempotency_key=f"reschedule-{call.id}",
                        confirmed=True,
                    ),
                )
                outcome = "appointment_changed"
                reply = "Your appointment has been moved and confirmed."
                summary_text = f"Rescheduled appointment to {appointment.starts_at.isoformat()}."
                follow_up = False
                follow_up_notes = None
            except HTTPException as exc:
                outcome = "message_taken"
                reply = (
                    "I couldn't safely change that appointment, so I've recorded it for follow-up."
                )
                summary_text = f"Reschedule could not be completed: {exc.detail}"
        else:
            outcome = "message_taken"
            reply = (
                "I need to identify the appointment and confirm the new time before changing it."
            )
            summary_text = (
                "Reschedule request needs appointment details, new time, and confirmation."
            )
    elif intent.intent == "cancellation":
        if payload.appointment_id and payload.confirmed:
            try:
                appointment = await booking.cancel(payload.appointment_id, confirmed=True)
                outcome = "cancelled"
                reply = "Your appointment has been cancelled."
                summary_text = "Caller explicitly confirmed cancellation; appointment cancelled."
                follow_up = False
                follow_up_notes = None
            except HTTPException as exc:
                outcome = "message_taken"
                reply = (
                    "I couldn't safely cancel that appointment, so I've recorded it for follow-up."
                )
                summary_text = f"Cancellation could not be completed: {exc.detail}"
        else:
            outcome = "message_taken"
            reply = (
                "I need to identify the appointment and get your confirmation before cancelling it."
            )
            summary_text = "Cancellation request needs appointment identification and confirmation."
    elif intent.intent == "enquiry":
        services = await ServiceRepository(db, auth.tenant_id).list()
        hours = await BusinessHoursRepository(db, auth.tenant_id).list()
        service_text = (
            ", ".join(s.name for s in services if s.is_active) or "services are not configured yet"
        )
        if "service" in payload.utterance.lower():
            reply = f"We currently have {service_text}."
        elif hours:
            open_days = [h for h in hours if not h.is_closed and h.opens_at and h.closes_at]
            if open_days:
                reply = "The salon hours are configured in our system. I can help with a booking as well."
            else:
                reply = (
                    "The salon's opening hours are not fully configured yet. I can take a message."
                )
        else:
            reply = f"{tenant.name} can help with {service_text}."
        outcome = "enquiry_resolved"
        summary_text = f"Answered a basic salon enquiry. Intent confidence {intent.confidence:.2f}."
        follow_up = False
        follow_up_notes = None
    elif intent.intent == "message":
        outcome = "message_taken"
        reply = "I've recorded your message for the salon to follow up."
        summary_text = f"Message requested: {payload.utterance[:240]}"
    else:
        outcome = "failed_fallback"

    ended = utcnow()
    call.status = "completed"
    call.outcome = outcome
    call.ended_at = ended
    call.duration_seconds = max(1, int((ended - started).total_seconds()))
    summary = CallSummary(
        tenant_id=auth.tenant_id,
        call_id=call.id,
        outcome=outcome,
        summary_text=summary_text,
        follow_up_required=follow_up,
        follow_up_notes=follow_up_notes,
        created_at=ended,
        updated_at=ended,
    )
    db.add(summary)
    if outcome == "message_taken":
        callback_phone = payload.customer_phone or payload.caller_number
        customer_name = payload.customer_name or "Caller"
        db.add(
            SalonMessage(
                tenant_id=auth.tenant_id,
                call_id=call.id,
                customer_name=customer_name,
                callback_phone=callback_phone,
                message_text=summary_text,
                status="new",
                created_at=ended,
                updated_at=ended,
            )
        )
    db.add(
        TranscriptMessage(
            tenant_id=auth.tenant_id,
            call_id=call.id,
            speaker="assistant",
            text=reply,
            sequence_number=2,
            is_final=True,
            created_at=ended,
        )
    )
    await db.commit()
    await db.refresh(call)
    await db.refresh(summary)
    return SimulatedCallOut(
        call=to_call_out(call, summary), receptionist_reply=reply, appointment=appointment
    )
