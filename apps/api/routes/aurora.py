"""Private Aurora event receiver and per-call Pipecat RTP bridge launcher."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.config import get_settings

router = APIRouter(tags=["aurora"], include_in_schema=False)
log = logging.getLogger(__name__)
settings = get_settings()


class AuroraEvent(BaseModel):
    event: str = Field(min_length=1, max_length=64)
    call_id: UUID | Literal["system"]
    details: dict[str, Any] = Field(default_factory=dict)


@dataclass
class _CallTask:
    transport: Any
    task: asyncio.Task | None = None
    ended_by_sip: bool = False


_active_calls: dict[str, _CallTask] = {}
_active_lock = asyncio.Lock()
_background_tasks: set[asyncio.Task] = set()


def _sip_user(value: object) -> str | None:
    """Extract a user/number from a SIP URI or display-name form."""
    if not isinstance(value, str) or len(value) > 512:
        return None
    match = re.search(r"sips?:([^@;>\s]+)", value, flags=re.IGNORECASE)
    return match.group(1)[:32] if match else None


def _ringing_values(details: dict[str, Any]) -> dict[str, Any]:
    codec = details.get("codec")
    if codec not in ("PCMA", "PCMU"):
        raise HTTPException(status_code=422, detail="Unsupported Aurora codec")
    payload_type = details.get("payload_type")
    expected = 8 if codec == "PCMA" else 0
    if type(payload_type) is not int or payload_type != expected:
        raise HTTPException(status_code=422, detail="Invalid static RTP payload type")
    ports: dict[str, int] = {}
    for name in ("ai_rtp_port", "trunk_rtp_port"):
        value = details.get(name)
        if type(value) is not int or not 1 <= value <= 65535:
            raise HTTPException(status_code=422, detail=f"Invalid {name}")
        ports[name] = value
    return {
        "codec": codec,
        "payload_type": payload_type,
        **ports,
        "caller_number": _sip_user(details.get("from")),
        "called_number": _sip_user(details.get("to")),
    }


async def _control(call_id: str, action: str, payload: dict[str, Any]) -> None:
    token = settings.aurora_control_api_token
    if not token:
        raise RuntimeError("AURORA_CONTROL_API_TOKEN is not configured")
    url = f"{settings.aurora_control_api_url.rstrip('/')}/v1/calls/{call_id}/{action}"
    async with httpx.AsyncClient(timeout=3.0) as client:
        response = await client.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
    if response.status_code >= 300:
        raise RuntimeError(f"Aurora {action} returned HTTP {response.status_code}")


async def _reject_unhandled_call(call_id: str, sip_status: int) -> None:
    try:
        await _control(call_id, "reject", {"sip_status": sip_status})
    except Exception:
        log.warning("Aurora call %s could not be rejected with SIP %s", call_id, sip_status)


def _schedule_reject(call_id: str, sip_status: int) -> None:
    task = asyncio.create_task(_reject_unhandled_call(call_id, sip_status))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _run_call(call_id: str, values: dict[str, Any], session: _CallTask) -> None:
    transport = session.transport
    pipeline: asyncio.Task | None = None
    accepted = False
    try:
        from pipecat.runner.types import RunnerArguments

        from apps.voice.bot import run_bot

        pipeline = asyncio.create_task(
            run_bot(
                transport,
                RunnerArguments(),
                external_call_id=f"aurora_{call_id}",
                caller_number=values["caller_number"],
                called_number=values["called_number"],
            ),
            name=f"aurora-pipecat-{call_id}",
        )
        pipeline_ready = asyncio.create_task(transport.pipeline_ready.wait())
        call_ended = asyncio.create_task(transport.closed.wait())
        done, pending = await asyncio.wait(
            (pipeline, pipeline_ready, call_ended),
            timeout=settings.aurora_startup_timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for waiter in pending:
            if waiter is not pipeline:
                waiter.cancel()
        await asyncio.gather(
            *(waiter for waiter in pending if waiter is not pipeline), return_exceptions=True
        )
        if call_ended in done:
            if session.ended_by_sip:
                return
            raise RuntimeError("Aurora RTP transport closed before the call was answered")
        if pipeline in done:
            pipeline.result()
            raise RuntimeError("Pipecat pipeline stopped before it was ready")
        if pipeline_ready not in done:
            raise TimeoutError("Pipecat pipeline did not become ready before the startup timeout")

        await _control(
            call_id,
            "accept",
            {"ai_rtp_host": transport.local_host, "ai_rtp_port": transport.local_port},
        )
        accepted = True
        transport.accepted.set()
        await pipeline
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Aurora call %s could not start Pipecat", call_id)
        try:
            if accepted and not session.ended_by_sip:
                await _control(call_id, "hangup", {})
            elif not session.ended_by_sip:
                await _control(call_id, "reject", {"sip_status": 503})
        except Exception:
            log.warning("Aurora call %s could not be cleaned up through the control API", call_id)
    finally:
        transport.close()
        if pipeline is not None and not pipeline.done():
            try:
                await asyncio.wait_for(asyncio.shield(pipeline), timeout=5.0)
            except TimeoutError:
                pipeline.cancel()
            except Exception:
                pass
        if pipeline is not None:
            await asyncio.gather(pipeline, return_exceptions=True)
        transport.dispose()
        async with _active_lock:
            if _active_calls.get(call_id) is session:
                _active_calls.pop(call_id, None)


@router.post("/internal/sip-events", status_code=202)
async def receive_aurora_event(event: AuroraEvent, request: Request) -> dict[str, str]:
    """Receive Aurora callbacks. Keep the API listener bound to loopback."""
    client_host = request.client.host if request.client else ""
    try:
        is_loopback = ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback and client_host != "100.105.4.49":
        raise HTTPException(status_code=403, detail="Aurora callback source is not trusted")
    if event.event in {"trunk.registered", "trunk.registration_failed"}:
        log.info("Aurora trunk event: %s status=%s", event.event, event.details.get("status"))
        return {"status": "accepted"}
    if not isinstance(event.call_id, UUID):
        raise HTTPException(status_code=422, detail="Call events require a UUID call_id")
    call_id = str(event.call_id)
    if event.event == "call.ringing":
        try:
            values = _ringing_values(event.details)
        except HTTPException:
            _schedule_reject(call_id, 488)
            raise
        if not settings.aurora_control_api_token or not settings.aurora_rtp_host:
            _schedule_reject(call_id, 503)
            raise HTTPException(
                status_code=503,
                detail="Aurora control token and RTP host must be configured",
            )
        if settings.aurora_max_active_calls < 1:
            _schedule_reject(call_id, 503)
            raise HTTPException(status_code=503, detail="Aurora call capacity is disabled")
        try:
            from services.voice.aurora_rtp_transport import AuroraRtpTransport
        except ImportError as exc:
            _schedule_reject(call_id, 503)
            raise HTTPException(status_code=503, detail="Pipecat voice extra is unavailable") from exc

        async with _active_lock:
            if call_id in _active_calls:
                return {"status": "duplicate"}
            if len(_active_calls) >= settings.aurora_max_active_calls:
                _schedule_reject(call_id, 503)
                raise HTTPException(status_code=503, detail="Pipecat call capacity is full")
            try:
                transport = AuroraRtpTransport(
                    call_id,
                    aurora_host=settings.aurora_rtp_host,
                    aurora_ai_port=values["ai_rtp_port"],
                    codec=values["codec"],
                    payload_type=values["payload_type"],
                    bind_host=settings.aurora_rtp_bind_host,
                    start_timeout=max(
                        30.0, settings.aurora_startup_timeout_seconds + 10.0
                    ),
                )
            except (OSError, ValueError) as exc:
                _schedule_reject(call_id, 503)
                raise HTTPException(status_code=503, detail="Could not allocate RTP socket") from exc
            session = _CallTask(transport=transport)
            _active_calls[call_id] = session
            session.task = asyncio.create_task(_run_call(call_id, values, session))
        return {"status": "accepted"}

    if event.event in ("call.ended", "call.cancel_received", "call.bye_received"):
        async with _active_lock:
            session = _active_calls.get(call_id)
            if session is not None:
                session.ended_by_sip = True
                session.transport.close()
    return {"status": "accepted"}
