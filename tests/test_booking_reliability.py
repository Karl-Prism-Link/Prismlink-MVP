from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from apps.api.schemas import AppointmentCreate
from database.models import AuditLog
from database.repositories import TenantRepository
from database.session import DBSession
from database.utils import utcnow
from services.booking import BookingService
from services.calendar.base import CalendarEvent
from tests.test_mvp import add_service, auth, register


class CompensatingCalendar:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def is_available(self, **_kwargs) -> bool:
        return True

    async def create_event(self, **_kwargs) -> CalendarEvent:
        now = utcnow()
        return CalendarEvent("remote-event-1", now, now)

    async def cancel_event(self, *, external_event_id: str) -> None:
        self.cancelled.append(external_event_id)


@pytest.mark.asyncio
async def test_create_compensates_calendar_event_when_local_commit_fails(client, monkeypatch):
    token = register(
        client, name="Reliable Salon", slug="reliable-salon", email="reliable@example.com"
    )
    service = add_service(client, token, "Cut", 30)
    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("reliable-salon")
        assert tenant is not None
        booking = BookingService(db, tenant.id)
        calendar = CompensatingCalendar()
        booking.calendar = calendar
        payload = AppointmentCreate(
            customer_name="Jamie Example",
            customer_phone="0210000000",
            service_id=service["id"],
            starts_at=utcnow() + timedelta(days=2),
            idempotency_key=f"compensate-{uuid4().hex}",
            confirmed=True,
            source="dashboard",
        )

        async def fail_commit() -> None:
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="database unavailable"):
            await booking.create(payload)
        assert calendar.cancelled == ["remote-event-1"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_cancellation_writes_audit_log(client):
    token = register(
        client, name="Audited Salon", slug="audited-salon", email="audited@example.com"
    )
    service = add_service(client, token, "Cut", 30)
    created = client.post(
        "/api/v1/appointments",
        headers=auth(token),
        json={
            "customer_name": "Jamie Example",
            "customer_phone": "0210000000",
            "service_id": service["id"],
            "starts_at": (utcnow() + timedelta(days=2)).isoformat(),
            "idempotency_key": "audit-cancel-001",
            "confirmed": True,
            "source": "dashboard",
        },
    )
    assert created.status_code == 201, created.text

    cancelled = client.post(
        f"/api/v1/appointments/{created.json()['id']}/cancel",
        headers=auth(token),
        json={"confirmed": True},
    )
    assert cancelled.status_code == 200, cancelled.text

    db = DBSession()
    try:
        rows = await db.execute(select(AuditLog).where(AuditLog.action == "appointment.cancelled"))
        audit = rows.scalar_one()
        assert audit.resource_id == created.json()["id"]
    finally:
        await db.close()
