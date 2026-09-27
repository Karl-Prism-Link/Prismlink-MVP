from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class CalendarEvent:
    external_event_id: str
    starts_at: datetime
    ends_at: datetime


class CalendarProvider(Protocol):
    async def is_available(
        self,
        *,
        tenant_id: UUID,
        starts_at: datetime,
        ends_at: datetime,
        staff_member_id: UUID | None,
        exclude_appointment_id: UUID | None = None,
    ) -> bool: ...

    async def create_event(
        self,
        *,
        tenant_id: UUID,
        summary: str,
        starts_at: datetime,
        ends_at: datetime,
        description: str,
    ) -> CalendarEvent: ...

    async def update_event(
        self,
        *,
        external_event_id: str,
        summary: str,
        starts_at: datetime,
        ends_at: datetime,
        description: str,
    ) -> CalendarEvent: ...

    async def cancel_event(self, *, external_event_id: str) -> None: ...
