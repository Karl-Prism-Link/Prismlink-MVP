from datetime import datetime, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from core.validation import CustomerInputError, normalise_customer_name, normalise_nz_phone


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class RegisterIn(BaseModel):
    salon_name: str = Field(min_length=2, max_length=160)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    timezone: str = "Pacific/Auckland"


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TenantOut(APIModel):
    id: UUID
    name: str
    slug: str
    timezone: str
    phone_number: str | None
    greeting: str
    status: str
    trial_ends_at: datetime


class TenantPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    timezone: str | None = Field(default=None, max_length=64)
    phone_number: str | None = Field(default=None, max_length=32)
    greeting: str | None = Field(default=None, min_length=2, max_length=1000)


class ServiceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    duration_minutes: int = Field(ge=5, le=480)
    price: int | None = Field(default=None, ge=0, le=100000)


class ServiceOut(APIModel):
    id: UUID
    name: str
    description: str | None
    duration_minutes: int
    price: int | None
    is_active: bool


class StaffCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)


class StaffOut(APIModel):
    id: UUID
    name: str
    is_active: bool


class BusinessHourIn(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    opens_at: time | None = None
    closes_at: time | None = None
    is_closed: bool = False


class BusinessHourOut(APIModel):
    id: UUID
    day_of_week: int
    opens_at: time | None
    closes_at: time | None
    is_closed: bool


class CalendarConnectIn(BaseModel):
    account_label: str | None = Field(default=None, max_length=160)
    external_calendar_id: str = Field(default="primary", max_length=256)


class CalendarStatusOut(APIModel):
    provider: str
    account_label: str | None
    external_calendar_id: str
    status: str
    connected_at: datetime
    last_verified_at: datetime


class GoogleOAuthStartOut(BaseModel):
    authorization_url: str


class AvailabilityIn(BaseModel):
    starts_at: datetime
    service_id: UUID
    staff_member_id: UUID | None = None


class AvailabilityOut(BaseModel):
    available: bool
    starts_at: datetime
    ends_at: datetime


class AppointmentCreate(BaseModel):
    customer_name: str = Field(min_length=2, max_length=160)
    customer_phone: str = Field(min_length=3, max_length=32)
    service_id: UUID
    staff_member_id: UUID | None = None
    starts_at: datetime
    idempotency_key: str = Field(min_length=8, max_length=160)
    confirmed: bool
    source: str = Field(default="voice", pattern=r"^(voice|dashboard)$")
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("customer_name")
    @classmethod
    def validate_customer_name(cls, value: str) -> str:
        try:
            return normalise_customer_name(value)
        except CustomerInputError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("customer_phone")
    @classmethod
    def validate_customer_phone(cls, value: str) -> str:
        try:
            return normalise_nz_phone(value)
        except CustomerInputError as exc:
            raise ValueError(str(exc)) from exc


class AppointmentMove(BaseModel):
    starts_at: datetime
    idempotency_key: str = Field(min_length=8, max_length=160)
    confirmed: bool


class AppointmentCancel(BaseModel):
    confirmed: bool


class AppointmentOut(APIModel):
    id: UUID
    service_id: UUID
    staff_member_id: UUID | None
    customer_name: str
    customer_phone: str
    starts_at: datetime
    ends_at: datetime
    status: str
    source: str
    external_event_id: str | None
    notes: str | None


class CallSimulateIn(BaseModel):
    caller_number: str | None = Field(default=None, max_length=32)
    utterance: str = Field(min_length=1, max_length=2000)
    appointment_id: UUID | None = None
    service_id: UUID | None = None
    staff_member_id: UUID | None = None
    starts_at: datetime | None = None
    customer_name: str | None = Field(default=None, max_length=160)
    customer_phone: str | None = Field(default=None, max_length=32)
    confirmed: bool = False

    @field_validator("customer_name")
    @classmethod
    def validate_optional_customer_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return normalise_customer_name(value)
        except CustomerInputError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("customer_phone")
    @classmethod
    def validate_optional_customer_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return normalise_nz_phone(value)
        except CustomerInputError as exc:
            raise ValueError(str(exc)) from exc


class CallSummaryOut(BaseModel):
    outcome: str
    summary_text: str
    follow_up_required: bool
    follow_up_notes: str | None


class CallOut(APIModel):
    id: UUID
    external_call_id: str
    caller_number: str | None
    called_number: str | None
    status: str
    outcome: str
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int | None
    summary: CallSummaryOut | None = None


class SimulatedCallOut(BaseModel):
    call: CallOut
    receptionist_reply: str
    appointment: AppointmentOut | None = None


class MessageOut(APIModel):
    id: UUID
    call_id: UUID
    customer_name: str | None
    callback_phone: str | None
    message_text: str
    status: str
    created_at: datetime
    updated_at: datetime


class MessageStatusPatch(BaseModel):
    status: Literal["new", "contacted", "resolved"]


class TranscriptMessageOut(APIModel):
    speaker: str
    text: str
    sequence_number: int
    created_at: datetime


class VoiceStatusOut(BaseModel):
    deepgram_configured: bool
    llm_provider: str
    llm_model: str
    llm_configured: bool
    groq_configured: bool
    openrouter_configured: bool
    configured_tenant_slug: str | None
    current_tenant_slug: str
    tenant_matches: bool
    browser_voice_ready: bool
    transport: str = "webrtc"
