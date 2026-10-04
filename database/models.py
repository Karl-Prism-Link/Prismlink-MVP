from __future__ import annotations

from datetime import datetime, time
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def uuid_pk() -> Mapped[UUID]:
    return mapped_column(primary_key=True, default=uuid4)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Pacific/Auckland")
    phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    greeting: Mapped[str] = mapped_column(
        Text, default="Thanks for calling. How can I help you today?"
    )
    status: Mapped[str] = mapped_column(String(32), default="trial")
    trial_started_at: Mapped[datetime]
    trial_ends_at: Mapped[datetime]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    users: Mapped[list[User]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_user_tenant_email"),)

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(32), default="owner")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    tenant: Mapped[Tenant] = relationship(back_populates="users")


class BusinessHour(Base):
    __tablename__ = "business_hours"
    __table_args__ = (UniqueConstraint("tenant_id", "day_of_week", name="uq_hours_tenant_day"),)

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    day_of_week: Mapped[int] = mapped_column(Integer)
    opens_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    closes_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class Service(Base):
    __tablename__ = "services"

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class StaffMember(Base):
    __tablename__ = "staff_members"

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class CalendarConnection(Base):
    __tablename__ = "calendar_connections"

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="google")
    account_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    external_calendar_id: Mapped[str] = mapped_column(String(256), default="primary")
    status: Mapped[str] = mapped_column(String(32), default="connected")
    credential_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    connected_at: Mapped[datetime]
    last_verified_at: Mapped[datetime]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    external_call_id: Mapped[str] = mapped_column(String(160), index=True)
    caller_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    called_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    outcome: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime]
    answered_at: Mapped[datetime | None] = mapped_column(nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime]


class TranscriptMessage(Base):
    __tablename__ = "transcript_messages"
    __table_args__ = (UniqueConstraint("call_id", "sequence_number", name="uq_call_sequence"),)

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id"), index=True)
    speaker: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    sequence_number: Mapped[int] = mapped_column(Integer)
    is_final: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime]


class CallSummary(Base):
    __tablename__ = "call_summaries"

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id"), unique=True, index=True)
    outcome: Mapped[str] = mapped_column(String(64))
    summary_text: Mapped[str] = mapped_column(Text)
    follow_up_required: Mapped[bool] = mapped_column(Boolean, default=False)
    follow_up_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class SalonMessage(Base):
    __tablename__ = "salon_messages"
    __table_args__ = (UniqueConstraint("tenant_id", "call_id", name="uq_message_tenant_call"),)

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id"), index=True)
    customer_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    callback_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="new", index=True)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_appointment_idempotency"),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    call_id: Mapped[UUID | None] = mapped_column(ForeignKey("calls.id"), nullable=True)
    service_id: Mapped[UUID] = mapped_column(ForeignKey("services.id"), index=True)
    staff_member_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_members.id"), nullable=True, index=True
    )
    calendar_connection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("calendar_connections.id"), nullable=True
    )
    external_event_id: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    customer_name: Mapped[str] = mapped_column(String(160))
    customer_phone: Mapped[str] = mapped_column(String(32))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="booked", index=True)
    source: Mapped[str] = mapped_column(String(32), default="voice")
    idempotency_key: Mapped[str] = mapped_column(String(160))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(128))
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str] = mapped_column(String(128))
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime]
