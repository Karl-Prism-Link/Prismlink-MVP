from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from apps.api.routes import aurora


def _request(client_host: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/internal/sip-events",
            "raw_path": b"/internal/sip-events",
            "query_string": b"",
            "headers": [],
            "client": (client_host, 5060),
            "server": ("127.0.0.1", 8000),
        }
    )


@pytest.mark.asyncio
async def test_aurora_trunk_registration_accepts_system_call_id(monkeypatch):
    monkeypatch.setattr(
        aurora,
        "settings",
        SimpleNamespace(aurora_event_token="test-event-token"),
    )
    event = aurora.AuroraEvent(event="trunk.registered", call_id="system", details={})

    result = await aurora.receive_aurora_event(
        event,
        _request("100.105.4.49"),
        authorization="Bearer test-event-token",
    )

    assert result == {"status": "accepted"}


@pytest.mark.asyncio
async def test_aurora_call_event_accepts_uuid_call_id(monkeypatch):
    monkeypatch.setattr(
        aurora,
        "settings",
        SimpleNamespace(aurora_event_token="test-event-token"),
    )
    call_id = uuid4()
    event = aurora.AuroraEvent(event="call.ended", call_id=call_id, details={})

    result = await aurora.receive_aurora_event(
        event,
        _request("100.105.4.49"),
        authorization="Bearer test-event-token",
    )

    assert result == {"status": "accepted"}


@pytest.mark.asyncio
async def test_aurora_rejects_system_id_for_call_events(monkeypatch):
    monkeypatch.setattr(
        aurora,
        "settings",
        SimpleNamespace(aurora_event_token="test-event-token"),
    )
    event = aurora.AuroraEvent(event="call.ended", call_id="system", details={})

    with pytest.raises(HTTPException) as exc:
        await aurora.receive_aurora_event(
            event,
            _request("100.105.4.49"),
            authorization="Bearer test-event-token",
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == "Call events require a UUID call_id"


@pytest.mark.asyncio
async def test_aurora_rejects_untrusted_callback_source(monkeypatch):
    monkeypatch.setattr(
        aurora,
        "settings",
        SimpleNamespace(aurora_event_token="test-event-token"),
    )
    event = aurora.AuroraEvent(event="trunk.registered", call_id="system", details={})

    with pytest.raises(HTTPException) as exc:
        await aurora.receive_aurora_event(
            event,
            _request("203.0.113.25"),
            authorization="Bearer test-event-token",
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_aurora_rejects_invalid_callback_token(monkeypatch):
    monkeypatch.setattr(
        aurora,
        "settings",
        SimpleNamespace(aurora_event_token="test-event-token"),
    )
    event = aurora.AuroraEvent(event="trunk.registered", call_id="system", details={})

    with pytest.raises(HTTPException) as exc:
        await aurora.receive_aurora_event(
            event,
            _request("100.105.4.49"),
            authorization="Bearer wrong-token",
        )

    assert exc.value.status_code == 401


def test_aurora_event_model_rejects_non_uuid_call_ids():
    with pytest.raises(ValidationError):
        aurora.AuroraEvent(event="call.ended", call_id="not-a-uuid", details={})
