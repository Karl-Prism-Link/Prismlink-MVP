from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select

from database.models import (
    Appointment,
    BusinessHour,
    CalendarConnection,
    Call,
    CallSummary,
    SalonMessage,
    Service,
    StaffMember,
    Tenant,
    TranscriptMessage,
    User,
)
from database.session import DBSession


class UserRepository:
    def __init__(self, db: DBSession) -> None:
        self.db = db

    async def by_email(self, email: str) -> User | None:
        result = await self.db.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()

    async def add(self, user: User) -> User:
        self.db.add(user)
        return user


class TenantRepository:
    def __init__(self, db: DBSession) -> None:
        self.db = db

    async def by_id(self, tenant_id: UUID) -> Tenant | None:
        return await self.db.get(Tenant, tenant_id)

    async def by_slug(self, slug: str) -> Tenant | None:
        result = await self.db.execute(select(Tenant).where(Tenant.slug == slug))
        return result.scalar_one_or_none()

    async def by_phone_number(self, phone_number: str) -> Tenant | None:
        result = await self.db.execute(select(Tenant).where(Tenant.phone_number == phone_number))
        return result.scalar_one_or_none()

    async def add(self, tenant: Tenant) -> Tenant:
        self.db.add(tenant)
        return tenant


class ServiceRepository:
    def __init__(self, db: DBSession, tenant_id: UUID) -> None:
        self.db = db
        self.tenant_id = tenant_id

    async def list(self) -> list[Service]:
        result = await self.db.execute(
            select(Service).where(Service.tenant_id == self.tenant_id).order_by(Service.name)
        )
        return list(result.scalars())

    async def get(self, service_id: UUID) -> Service | None:
        result = await self.db.execute(
            select(Service).where(
                Service.id == service_id,
                Service.tenant_id == self.tenant_id,
            )
        )
        return result.scalar_one_or_none()


class StaffRepository:
    def __init__(self, db: DBSession, tenant_id: UUID) -> None:
        self.db = db
        self.tenant_id = tenant_id

    async def list(self) -> list[StaffMember]:
        result = await self.db.execute(
            select(StaffMember)
            .where(StaffMember.tenant_id == self.tenant_id)
            .order_by(StaffMember.name)
        )
        return list(result.scalars())

    async def get(self, staff_id: UUID) -> StaffMember | None:
        result = await self.db.execute(
            select(StaffMember).where(
                StaffMember.id == staff_id,
                StaffMember.tenant_id == self.tenant_id,
            )
        )
        return result.scalar_one_or_none()


class BusinessHoursRepository:
    def __init__(self, db: DBSession, tenant_id: UUID) -> None:
        self.db = db
        self.tenant_id = tenant_id

    async def list(self) -> list[BusinessHour]:
        result = await self.db.execute(
            select(BusinessHour)
            .where(BusinessHour.tenant_id == self.tenant_id)
            .order_by(BusinessHour.day_of_week)
        )
        return list(result.scalars())


class CalendarConnectionRepository:
    def __init__(self, db: DBSession, tenant_id: UUID) -> None:
        self.db = db
        self.tenant_id = tenant_id

    async def active(self) -> CalendarConnection | None:
        result = await self.db.execute(
            select(CalendarConnection).where(
                CalendarConnection.tenant_id == self.tenant_id,
                CalendarConnection.status == "connected",
            )
        )
        return result.scalar_one_or_none()

    async def latest(self) -> CalendarConnection | None:
        result = await self.db.execute(
            select(CalendarConnection)
            .where(CalendarConnection.tenant_id == self.tenant_id)
            .order_by(CalendarConnection.updated_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


class AppointmentRepository:
    def __init__(self, db: DBSession, tenant_id: UUID) -> None:
        self.db = db
        self.tenant_id = tenant_id

    async def list(self, *, limit: int = 100) -> list[Appointment]:
        result = await self.db.execute(
            select(Appointment)
            .where(Appointment.tenant_id == self.tenant_id)
            .order_by(Appointment.starts_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def get(self, appointment_id: UUID) -> Appointment | None:
        result = await self.db.execute(
            select(Appointment).where(
                Appointment.id == appointment_id,
                Appointment.tenant_id == self.tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def by_idempotency_key(self, key: str) -> Appointment | None:
        result = await self.db.execute(
            select(Appointment).where(
                Appointment.tenant_id == self.tenant_id,
                Appointment.idempotency_key == key,
            )
        )
        return result.scalar_one_or_none()

    async def for_customer(
        self, *, customer_phone: str, customer_name: str | None = None, limit: int = 10
    ) -> list[Appointment]:
        conditions = [
            Appointment.tenant_id == self.tenant_id,
            Appointment.customer_phone == customer_phone,
            Appointment.status != "cancelled",
        ]
        if customer_name:
            conditions.append(Appointment.customer_name.ilike(f"%{customer_name.strip()}%"))
        result = await self.db.execute(
            select(Appointment)
            .where(and_(*conditions))
            .order_by(Appointment.starts_at.asc())
            .limit(limit)
        )
        return list(result.scalars())

    async def overlapping(
        self,
        *,
        starts_at: datetime,
        ends_at: datetime,
        staff_member_id: UUID | None,
        exclude_id: UUID | None = None,
    ) -> list[Appointment]:
        conditions = [
            Appointment.tenant_id == self.tenant_id,
            Appointment.status != "cancelled",
            Appointment.starts_at < ends_at,
            Appointment.ends_at > starts_at,
        ]
        if staff_member_id is not None:
            conditions.append(
                or_(
                    Appointment.staff_member_id == staff_member_id,
                    Appointment.staff_member_id.is_(None),
                )
            )
        if exclude_id is not None:
            conditions.append(Appointment.id != exclude_id)
        result = await self.db.execute(select(Appointment).where(and_(*conditions)))
        return list(result.scalars())


class CallRepository:
    def __init__(self, db: DBSession, tenant_id: UUID) -> None:
        self.db = db
        self.tenant_id = tenant_id

    async def list(self, *, limit: int = 100) -> list[Call]:
        result = await self.db.execute(
            select(Call)
            .where(Call.tenant_id == self.tenant_id)
            .order_by(Call.started_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def get(self, call_id: UUID) -> Call | None:
        result = await self.db.execute(
            select(Call).where(Call.id == call_id, Call.tenant_id == self.tenant_id)
        )
        return result.scalar_one_or_none()

    async def summary(self, call_id: UUID) -> CallSummary | None:
        result = await self.db.execute(
            select(CallSummary).where(
                CallSummary.call_id == call_id,
                CallSummary.tenant_id == self.tenant_id,
            )
        )
        return result.scalar_one_or_none()


class MessageRepository:
    def __init__(self, db: DBSession, tenant_id: UUID) -> None:
        self.db = db
        self.tenant_id = tenant_id

    async def list(self, *, limit: int = 100) -> list[SalonMessage]:
        result = await self.db.execute(
            select(SalonMessage)
            .where(SalonMessage.tenant_id == self.tenant_id)
            .order_by(SalonMessage.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def get(self, message_id: UUID) -> SalonMessage | None:
        result = await self.db.execute(
            select(SalonMessage).where(
                SalonMessage.id == message_id,
                SalonMessage.tenant_id == self.tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def by_call(self, call_id: UUID) -> SalonMessage | None:
        result = await self.db.execute(
            select(SalonMessage).where(
                SalonMessage.call_id == call_id,
                SalonMessage.tenant_id == self.tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def transcript(self, call_id: UUID) -> list[TranscriptMessage]:
        result = await self.db.execute(
            select(TranscriptMessage)
            .where(
                TranscriptMessage.call_id == call_id,
                TranscriptMessage.tenant_id == self.tenant_id,
            )
            .order_by(TranscriptMessage.sequence_number.asc())
        )
        return list(result.scalars())
