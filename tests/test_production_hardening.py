from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from apps.api import rate_limit
from apps.api.rate_limit import InMemoryRateLimiter
from apps.api.schemas import AppointmentCreate, CallSimulateIn
from core.config import DEFAULT_JWT_SECRET, Settings, validate_runtime_settings


def appointment_payload() -> dict:
    return {
        "customer_name": "  Mere   O'Connor  ",
        "customer_phone": "+64 21 123 4567",
        "service_id": "00000000-0000-0000-0000-000000000001",
        "starts_at": "2026-12-01T10:00:00+13:00",
        "idempotency_key": "safe-booking-key",
        "confirmed": True,
    }


def test_customer_data_is_normalised_for_booking() -> None:
    payload = AppointmentCreate(**appointment_payload())

    assert payload.customer_name == "Mere O'Connor"
    assert payload.customer_phone == "0211234567"


def test_customer_data_rejects_invalid_booking_input() -> None:
    bad_phone = appointment_payload() | {"customer_phone": "not a phone"}
    bad_name = appointment_payload() | {"customer_name": "Mere 123"}

    with pytest.raises(ValidationError):
        AppointmentCreate(**bad_phone)
    with pytest.raises(ValidationError):
        CallSimulateIn(utterance="Book me in", customer_name=bad_name["customer_name"])


def test_production_requires_independent_strong_secrets() -> None:
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        validate_runtime_settings(
            Settings(
                _env_file=None,
                app_env="production",
                jwt_secret_key=DEFAULT_JWT_SECRET,
                credential_encryption_key=None,
            )
        )

    validate_runtime_settings(
        Settings(
            _env_file=None,
            app_env="production",
            jwt_secret_key="a" * 32,
            credential_encryption_key="b" * 32,
        )
    )


def test_in_memory_rate_limiter_reports_retry_after() -> None:
    now = [100.0]
    limiter = InMemoryRateLimiter(clock=lambda: now[0])

    assert limiter.retry_after("login:127.0.0.1", limit=2, window_seconds=60) is None
    assert limiter.retry_after("login:127.0.0.1", limit=2, window_seconds=60) is None
    assert limiter.retry_after("login:127.0.0.1", limit=2, window_seconds=60) == 60
    now[0] += 61
    assert limiter.retry_after("login:127.0.0.1", limit=2, window_seconds=60) is None


@pytest.mark.asyncio
async def test_auth_limit_returns_429_with_retry_after(monkeypatch) -> None:
    request = Request({"type": "http", "headers": [], "client": ("198.51.100.10", 1234)})
    test_limiter = InMemoryRateLimiter()
    monkeypatch.setattr(rate_limit, "limiter", test_limiter)
    monkeypatch.setattr(rate_limit, "get_settings", lambda: Settings(app_env="development"))

    await rate_limit.enforce_auth_rate_limit(request, scope="login", limit=1, window_seconds=60)
    with pytest.raises(HTTPException) as exc_info:
        await rate_limit.enforce_auth_rate_limit(request, scope="login", limit=1, window_seconds=60)

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "60"}


def test_request_middleware_adds_correlation_id(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
