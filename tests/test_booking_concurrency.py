import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from apps.api.schemas import AppointmentCreate
from database.repositories import TenantRepository
from database.session import DBSession
from database.utils import utcnow
from services.booking import BookingService
from services.calendar.base import CalendarEvent
from tests.test_mvp import add_service, register


class RaceCalendar:
    def __init__(self) -> None:
        self.booked = False
        self.create_calls = 0

    async def is_available(self, **_kwargs) -> bool:
        return not self.booked

    async def create_event(self, **_kwargs) -> CalendarEvent:
        await asyncio.sleep(0.02)
        self.booked = True
        self.create_calls += 1
        return CalendarEvent(
            external_event_id=f"race-{self.create_calls}",
            starts_at=utcnow(),
            ends_at=utcnow(),
        )


@pytest.mark.asyncio
async def test_simultaneous_booking_attempts_only_create_one_event(client):
    token = register(client, name="Race Guard Salon", slug="race-guard", email="race@example.com")
    service = add_service(client, token, "Cut", 30)
    db_one = DBSession()
    db_two = DBSession()
    try:
        tenant = await TenantRepository(db_one).by_slug("race-guard")
        assert tenant is not None
        starts_at = utcnow() + timedelta(days=2)
        payloads = [
            AppointmentCreate(
                customer_name=name,
                customer_phone=phone,
                service_id=service["id"],
                starts_at=starts_at,
                idempotency_key=f"race-{uuid4().hex}",
                confirmed=True,
                source="dashboard",
            )
            for name, phone in [("Alex Example", "0210000001"), ("Blair Example", "0210000002")]
        ]
        calendar = RaceCalendar()
        first = BookingService(db_one, tenant.id)
        second = BookingService(db_two, tenant.id)
        first.calendar = calendar
        second.calendar = calendar

        results = await asyncio.gather(
            first.create(payloads[0]), second.create(payloads[1]), return_exceptions=True
        )

        assert sum(not isinstance(result, Exception) for result in results) == 1
        assert any(getattr(result, "status_code", None) == 409 for result in results)
        assert calendar.create_calls == 1
    finally:
        await db_one.close()
        await db_two.close()
