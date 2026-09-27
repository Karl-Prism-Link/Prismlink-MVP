from uuid import UUID

from core.config import get_settings
from database.repositories import AppointmentRepository
from database.session import DBSession
from services.calendar.base import CalendarProvider
from services.calendar.google import GoogleCalendarProvider
from services.calendar.mock import MockCalendarProvider


def build_calendar_provider(
    db: DBSession,
    tenant_id: UUID,
    appointments: AppointmentRepository,
) -> CalendarProvider:
    if get_settings().calendar_provider == "google":
        return GoogleCalendarProvider(db, tenant_id)
    return MockCalendarProvider(appointments)
