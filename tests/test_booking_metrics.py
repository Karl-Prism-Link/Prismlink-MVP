from datetime import UTC, datetime, timedelta


def _register(client, *, name: str, slug: str, email: str):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "salon_name": name,
            "slug": slug,
            "email": email,
            "password": "StrongPass123!",
            "timezone": "Pacific/Auckland",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def test_booking_prometheus_metrics_cover_create_idempotency_and_confirmation(client):
    from monitoring import (
        BOOKING_ATTEMPTS,
        BOOKING_CREATED,
        BOOKING_FAILURES,
        BOOKING_IDEMPOTENT_REPLAYS,
    )

    token = _register(
        client,
        name="Metrics Salon",
        slug="booking-metrics-salon",
        email="booking-metrics@example.com",
    )
    headers = {"Authorization": f"Bearer {token}"}
    service_response = client.post(
        "/api/v1/salon/services",
        headers=headers,
        json={"name": "Haircut", "duration_minutes": 45},
    )
    assert service_response.status_code == 201, service_response.text
    service_id = service_response.json()["id"]
    payload = {
        "customer_name": "Test Customer",
        "customer_phone": "0210000000",
        "service_id": service_id,
        "starts_at": (datetime.now(UTC) + timedelta(days=4)).replace(microsecond=0).isoformat(),
        "idempotency_key": "metrics-booking-001",
        "confirmed": False,
        "source": "voice",
    }

    before_attempts = BOOKING_ATTEMPTS._value.get()
    before_created = BOOKING_CREATED._value.get()
    before_replays = BOOKING_IDEMPOTENT_REPLAYS._value.get()
    before_confirmation_failures = BOOKING_FAILURES.labels(
        reason="confirmation_required"
    )._value.get()

    denied = client.post("/api/v1/appointments", headers=headers, json=payload)
    assert denied.status_code == 409

    payload["confirmed"] = True
    created = client.post("/api/v1/appointments", headers=headers, json=payload)
    replayed = client.post("/api/v1/appointments", headers=headers, json=payload)
    assert created.status_code == 201, created.text
    assert replayed.status_code == 201, replayed.text
    assert created.json()["id"] == replayed.json()["id"]

    assert BOOKING_ATTEMPTS._value.get() - before_attempts == 3
    assert BOOKING_CREATED._value.get() - before_created == 1
    assert BOOKING_IDEMPOTENT_REPLAYS._value.get() - before_replays == 1
    assert (
        BOOKING_FAILURES.labels(reason="confirmation_required")._value.get()
        - before_confirmation_failures
        == 1
    )

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    for metric in (
        "booking_attempts_total",
        "booking_created_total",
        "booking_idempotent_replays_total",
        "booking_failures_total",
        "booking_availability_checks_total",
    ):
        assert metric in metrics.text
