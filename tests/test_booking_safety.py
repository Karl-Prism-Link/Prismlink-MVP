from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from tests.test_mvp import add_service, auth, register


def _next_weekday_at(hour: int) -> datetime:
    timezone = ZoneInfo("Pacific/Auckland")
    candidate = datetime.now(timezone).replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= datetime.now(timezone):
        candidate += timedelta(days=1)
    while candidate.weekday() > 4:
        candidate += timedelta(days=1)
    return candidate


def _configure_weekday_hours(client, token: str) -> None:
    hours = [
        {"day_of_week": day, "opens_at": "09:00", "closes_at": "17:00", "is_closed": day > 4}
        for day in range(7)
    ]
    response = client.put("/api/v1/salon/hours", headers=auth(token), json=hours)
    assert response.status_code == 200, response.text


def _booking_payload(service_id: str, starts_at: datetime, *, key: str) -> dict:
    return {
        "customer_name": "Jamie Example",
        "customer_phone": "0210000000",
        "service_id": service_id,
        "starts_at": starts_at.isoformat(),
        "idempotency_key": key,
        "confirmed": True,
        "source": "dashboard",
    }


def test_direct_booking_rejects_past_and_outside_hours(client):
    token = register(
        client,
        name="Booking Safety Salon",
        slug="booking-safety",
        email="safe@example.com",
    )
    service = add_service(client, token, "Cut", 30)
    _configure_weekday_hours(client, token)

    past = client.post(
        "/api/v1/appointments",
        headers=auth(token),
        json=_booking_payload(
            service["id"],
            datetime.now(UTC) - timedelta(minutes=30),
            key="past-booking-001",
        ),
    )
    assert past.status_code == 422
    assert past.json()["detail"]["code"] == "APPOINTMENT_IN_PAST"

    before_open = _next_weekday_at(8)
    outside = client.post(
        "/api/v1/appointments",
        headers=auth(token),
        json=_booking_payload(service["id"], before_open, key="outside-booking-001"),
    )
    assert outside.status_code == 422
    assert outside.json()["detail"]["code"] == "OUTSIDE_BUSINESS_HOURS"


def test_direct_reschedule_rejects_outside_hours(client):
    token = register(
        client,
        name="Reschedule Safety Salon",
        slug="reschedule-safety",
        email="reschedule@example.com",
    )
    service = add_service(client, token, "Cut", 30)
    _configure_weekday_hours(client, token)
    booked_at = _next_weekday_at(10)
    created = client.post(
        "/api/v1/appointments",
        headers=auth(token),
        json=_booking_payload(service["id"], booked_at, key="reschedule-create-001"),
    )
    assert created.status_code == 201, created.text

    moved = client.post(
        f"/api/v1/appointments/{created.json()['id']}/reschedule",
        headers=auth(token),
        json={
            "starts_at": _next_weekday_at(18).isoformat(),
            "idempotency_key": "reschedule-outside-001",
            "confirmed": True,
        },
    )
    assert moved.status_code == 422
    assert moved.json()["detail"]["code"] == "OUTSIDE_BUSINESS_HOURS"
