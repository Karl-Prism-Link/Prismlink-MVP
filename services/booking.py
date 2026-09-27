import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status
from sqlalchemy import text

from database.models import Appointment, AuditLog
from database.repositories import (
    AppointmentRepository,
    BusinessHoursRepository,
    ServiceRepository,
    StaffRepository,
    TenantRepository,
)
from database.session import DBSession
from database.utils import utcnow
from services.calendar.factory import build_calendar_provider
from services.calendar.google import GoogleCalendarProviderError
from services.service_names import canonical_service_name

logger = logging.getLogger(__name__)


class BookingService:
    _booking_locks: dict[UUID, asyncio.Lock] = {}
    _booking_locks_guard = asyncio.Lock()

    def __init__(self, db: DBSession, tenant_id: UUID, user_id: UUID | None = None) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.appointments = AppointmentRepository(db, tenant_id)
        self.services = ServiceRepository(db, tenant_id)
        self.staff = StaffRepository(db, tenant_id)
        self.hours = BusinessHoursRepository(db, tenant_id)
        self.tenants = TenantRepository(db)
        self.calendar = build_calendar_provider(db, tenant_id, self.appointments)

    @asynccontextmanager
    async def _booking_guard(self):
        """Serialize booking changes for a tenant across local and PostgreSQL runs."""
        async with self._booking_locks_guard:
            lock = self._booking_locks.setdefault(self.tenant_id, asyncio.Lock())
        async with lock:
            if self.db.dialect_name == "postgresql":
                # The transaction-scoped database lock also coordinates separate app workers.
                await self.db.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": self.tenant_id.int % (2**63)},
                )
            yield

    async def _validate_booking_window(self, starts_at: datetime, ends_at: datetime) -> None:
        """Reject booking windows that cannot safely be booked for this salon."""
        if starts_at.tzinfo is None or starts_at.utcoffset() is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "DATETIME_TIMEZONE_REQUIRED",
                    "message": "Appointment times must include a timezone.",
                },
            )
        if starts_at <= utcnow():
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "APPOINTMENT_IN_PAST",
                    "message": "Appointments must start in the future.",
                },
            )

        tenant = await self.tenants.by_id(self.tenant_id)
        if tenant is None:
            raise HTTPException(status_code=404, detail="Salon not found")
        try:
            timezone = ZoneInfo(tenant.timezone)
        except ZoneInfoNotFoundError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "INVALID_SALON_TIMEZONE",
                    "message": (
                        "The salon timezone is invalid. Update the salon profile before booking."
                    ),
                },
            ) from exc

        configured = await self.hours.list()
        local_start = starts_at.astimezone(timezone)
        local_end = ends_at.astimezone(timezone)
        day = next((row for row in configured if row.day_of_week == local_start.weekday()), None)

        # New salons are not blocked until they choose their opening hours.
        if not configured or day is None:
            return
        if (
            day.is_closed
            or day.opens_at is None
            or day.closes_at is None
            or local_start.date() != local_end.date()
            or local_start.time().replace(tzinfo=None) < day.opens_at
            or local_end.time().replace(tzinfo=None) > day.closes_at
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "OUTSIDE_BUSINESS_HOURS",
                    "message": (
                        "The appointment must be within the salon's configured business hours."
                    ),
                },
            )

    async def availability(
        self, *, starts_at, service_id: UUID, staff_member_id: UUID | None = None
    ) -> tuple[bool, object]:
        service = await self.services.get(service_id)
        if service is None or not service.is_active:
            raise HTTPException(status_code=404, detail="Service not found")
        if staff_member_id is not None:
            staff = await self.staff.get(staff_member_id)
            if staff is None or not staff.is_active:
                raise HTTPException(status_code=404, detail="Staff member not found")
        ends_at = starts_at + timedelta(minutes=service.duration_minutes)
        await self._validate_booking_window(starts_at, ends_at)
        try:
            available = await self.calendar.is_available(
                tenant_id=self.tenant_id,
                starts_at=starts_at,
                ends_at=ends_at,
                staff_member_id=staff_member_id,
            )
        except GoogleCalendarProviderError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "CALENDAR_UNAVAILABLE", "message": str(exc)},
            ) from exc
        return available, ends_at

    async def create(self, payload, *, call_id: UUID | None = None) -> Appointment:
        if not payload.confirmed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "CONFIRMATION_REQUIRED",
                    "message": "Caller confirmation is required.",
                },
            )
        async with self._booking_guard():
            return await self._create_locked(payload, call_id=call_id)

    async def _create_locked(self, payload, *, call_id: UUID | None = None) -> Appointment:
        prior = await self.appointments.by_idempotency_key(payload.idempotency_key)
        if prior is not None:
            return prior
        service = await self.services.get(payload.service_id)
        if service is None or not service.is_active:
            raise HTTPException(status_code=404, detail="Service not found")
        if payload.staff_member_id is not None:
            staff = await self.staff.get(payload.staff_member_id)
            if staff is None or not staff.is_active:
                raise HTTPException(status_code=404, detail="Staff member not found")
        ends_at = payload.starts_at + timedelta(minutes=service.duration_minutes)
        await self._validate_booking_window(payload.starts_at, ends_at)
        try:
            is_available = await self.calendar.is_available(
                tenant_id=self.tenant_id,
                starts_at=payload.starts_at,
                ends_at=ends_at,
                staff_member_id=payload.staff_member_id,
            )
        except GoogleCalendarProviderError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "CALENDAR_UNAVAILABLE", "message": str(exc)},
            ) from exc
        if not is_available:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "APPOINTMENT_CONFLICT",
                    "message": "That time is no longer available.",
                },
            )
        try:
            event = await self.calendar.create_event(
                tenant_id=self.tenant_id,
                summary=f"{canonical_service_name(service.name)} - {payload.customer_name}",
                starts_at=payload.starts_at,
                ends_at=ends_at,
                description=f"Booked by PRISM LINK. Customer phone: {payload.customer_phone}",
            )
        except GoogleCalendarProviderError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "CALENDAR_UNAVAILABLE", "message": str(exc)},
            ) from exc
        try:
            now = utcnow()
            appointment = Appointment(
                tenant_id=self.tenant_id,
                call_id=call_id,
                service_id=payload.service_id,
                staff_member_id=payload.staff_member_id,
                external_event_id=event.external_event_id,
                customer_name=payload.customer_name,
                customer_phone=payload.customer_phone,
                starts_at=payload.starts_at,
                ends_at=ends_at,
                status="booked",
                source=payload.source,
                idempotency_key=payload.idempotency_key,
                notes=payload.notes,
                created_at=now,
                updated_at=now,
            )
            self.db.add(appointment)
            await self.db.flush()
            self.db.add(
                AuditLog(
                    tenant_id=self.tenant_id,
                    user_id=self.user_id,
                    action="appointment.created",
                    resource_type="appointment",
                    resource_id=str(appointment.id),
                    metadata_json=None,
                    created_at=now,
                )
            )
            await self.db.commit()
            await self.db.refresh(appointment)
            return appointment
        except Exception:
            await self.db.rollback()
            try:
                await self.calendar.cancel_event(external_event_id=event.external_event_id)
            except Exception:
                logger.exception(
                    "Could not compensate calendar event after local appointment persistence failed"
                )
            raise

    async def reschedule(self, appointment_id: UUID, payload) -> Appointment:
        if not payload.confirmed:
            raise HTTPException(status_code=409, detail="Confirmation required")
        async with self._booking_guard():
            return await self._reschedule_locked(appointment_id, payload)

    async def _reschedule_locked(self, appointment_id: UUID, payload) -> Appointment:
        appointment = await self.appointments.get(appointment_id)
        if appointment is None:
            raise HTTPException(status_code=404, detail="Appointment not found")
        if appointment.status == "cancelled":
            raise HTTPException(
                status_code=409,
                detail="Cancelled appointments cannot be rescheduled",
            )
        service = await self.services.get(appointment.service_id)
        if service is None:
            raise HTTPException(status_code=409, detail="Appointment service is unavailable")
        ends_at = payload.starts_at + timedelta(minutes=service.duration_minutes)
        await self._validate_booking_window(payload.starts_at, ends_at)
        try:
            is_available = await self.calendar.is_available(
                tenant_id=self.tenant_id,
                starts_at=payload.starts_at,
                ends_at=ends_at,
                staff_member_id=appointment.staff_member_id,
                exclude_appointment_id=appointment.id,
            )
        except GoogleCalendarProviderError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "CALENDAR_UNAVAILABLE", "message": str(exc)},
            ) from exc
        if not is_available:
            raise HTTPException(status_code=409, detail="That time is no longer available")
        if appointment.external_event_id:
            try:
                await self.calendar.update_event(
                    external_event_id=appointment.external_event_id,
                    summary=f"{canonical_service_name(service.name)} - {appointment.customer_name}",
                    starts_at=payload.starts_at,
                    ends_at=ends_at,
                    description=(
                        f"Rescheduled by PRISM LINK. Customer phone: {appointment.customer_phone}"
                    ),
                )
            except GoogleCalendarProviderError as exc:
                raise HTTPException(
                    status_code=503,
                    detail={"code": "CALENDAR_UNAVAILABLE", "message": str(exc)},
                ) from exc
        appointment.starts_at = payload.starts_at
        appointment.ends_at = ends_at
        appointment.status = "rescheduled"
        appointment.idempotency_key = payload.idempotency_key
        appointment.updated_at = utcnow()
        await self.db.commit()
        await self.db.refresh(appointment)
        return appointment

    async def cancel(self, appointment_id: UUID, *, confirmed: bool) -> Appointment:
        if not confirmed:
            raise HTTPException(status_code=409, detail="Confirmation required")
        async with self._booking_guard():
            return await self._cancel_locked(appointment_id)

    async def _cancel_locked(self, appointment_id: UUID) -> Appointment:
        appointment = await self.appointments.get(appointment_id)
        if appointment is None:
            raise HTTPException(status_code=404, detail="Appointment not found")
        if appointment.status == "cancelled":
            return appointment
        if appointment.external_event_id:
            try:
                await self.calendar.cancel_event(external_event_id=appointment.external_event_id)
            except GoogleCalendarProviderError as exc:
                raise HTTPException(
                    status_code=503,
                    detail={"code": "CALENDAR_UNAVAILABLE", "message": str(exc)},
                ) from exc
        appointment.status = "cancelled"
        now = utcnow()
        appointment.updated_at = now
        self.db.add(
            AuditLog(
                tenant_id=self.tenant_id,
                user_id=self.user_id,
                action="appointment.cancelled",
                resource_type="appointment",
                resource_id=str(appointment.id),
                metadata_json=None,
                created_at=now,
            )
        )
        await self.db.commit()
        await self.db.refresh(appointment)
        return appointment
