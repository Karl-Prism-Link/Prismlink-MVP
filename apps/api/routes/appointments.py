from uuid import UUID

from fastapi import APIRouter, Depends

from apps.api.dependencies import AuthContext, get_auth_context
from apps.api.schemas import (
    AppointmentCancel,
    AppointmentCreate,
    AppointmentMove,
    AppointmentOut,
    AvailabilityIn,
    AvailabilityOut,
)
from database.repositories import AppointmentRepository
from database.session import DBSession, get_db
from services.booking import BookingService

router = APIRouter(prefix="/api/v1/appointments", tags=["appointments"])


@router.get("", response_model=list[AppointmentOut])
async def list_appointments(
    auth: AuthContext = Depends(get_auth_context), db: DBSession = Depends(get_db)
):
    return await AppointmentRepository(db, auth.tenant_id).list()


@router.get("/{appointment_id}", response_model=AppointmentOut)
async def get_appointment(
    appointment_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: DBSession = Depends(get_db),
):
    appointment = await AppointmentRepository(db, auth.tenant_id).get(appointment_id)
    if appointment is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Appointment not found")
    return appointment


@router.post("/availability", response_model=AvailabilityOut)
async def availability(
    payload: AvailabilityIn,
    auth: AuthContext = Depends(get_auth_context),
    db: DBSession = Depends(get_db),
) -> AvailabilityOut:
    available, ends_at = await BookingService(db, auth.tenant_id, auth.user_id).availability(
        starts_at=payload.starts_at,
        service_id=payload.service_id,
        staff_member_id=payload.staff_member_id,
    )
    return AvailabilityOut(available=available, starts_at=payload.starts_at, ends_at=ends_at)


@router.post("", response_model=AppointmentOut, status_code=201)
async def create_appointment(
    payload: AppointmentCreate,
    auth: AuthContext = Depends(get_auth_context),
    db: DBSession = Depends(get_db),
):
    return await BookingService(db, auth.tenant_id, auth.user_id).create(payload)


@router.post("/{appointment_id}/reschedule", response_model=AppointmentOut)
async def reschedule_appointment(
    appointment_id: UUID,
    payload: AppointmentMove,
    auth: AuthContext = Depends(get_auth_context),
    db: DBSession = Depends(get_db),
):
    return await BookingService(db, auth.tenant_id, auth.user_id).reschedule(
        appointment_id, payload
    )


@router.post("/{appointment_id}/cancel", response_model=AppointmentOut)
async def cancel_appointment(
    appointment_id: UUID,
    payload: AppointmentCancel,
    auth: AuthContext = Depends(get_auth_context),
    db: DBSession = Depends(get_db),
):
    return await BookingService(db, auth.tenant_id, auth.user_id).cancel(
        appointment_id, confirmed=payload.confirmed
    )
