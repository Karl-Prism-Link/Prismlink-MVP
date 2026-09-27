from datetime import datetime
from uuid import UUID, uuid4

from database.repositories import AppointmentRepository
from services.calendar.base import CalendarEvent


class MockCalendarProvider:
    """Local MVP calendar adapter.

    Availability is checked against PRISM LINK appointment records. This keeps the
    full booking workflow runnable without external credentials while preserving
    the provider boundary needed for Google Calendar.
    """

    def __init__(self, appointments: AppointmentRepository) -> None:
        self.appointments = appointments

    async def is_available(
        self,
        *,
        tenant_id: UUID,
        starts_at: datetime,
        ends_at: datetime,
        staff_member_id: UUID | None,
        exclude_appointment_id: UUID | None = None,
    ) -> bool:
        overlaps = await self.appointments.overlapping(
            starts_at=starts_at,
            ends_at=ends_at,
            staff_member_id=staff_member_id,
            exclude_id=exclude_appointment_id,
        )
        return not overlaps

    async def create_event(
        self,
        *,
        tenant_id: UUID,
        summary: str,
        starts_at: datetime,
        ends_at: datetime,
        description: str,
    ) -> CalendarEvent:
        return CalendarEvent(
            external_event_id=f"mock_{uuid4().hex}",
            starts_at=starts_at,
            ends_at=ends_at,
        )

    async def update_event(
        self,
        *,
        external_event_id: str,
        summary: str,
        starts_at: datetime,
        ends_at: datetime,
        description: str,
    ) -> CalendarEvent:
        return CalendarEvent(
            external_event_id=external_event_id,
            starts_at=starts_at,
            ends_at=ends_at,
        )

    async def cancel_event(self, *, external_event_id: str) -> None:
        return None
