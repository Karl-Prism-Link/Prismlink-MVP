import importlib.util

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import select

from apps.api.dependencies import AuthContext, get_auth_context
from apps.api.schemas import VoiceStatusOut
from core.config import get_settings
from database.models import Tenant
from database.session import DBSession, get_db

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])
settings = get_settings()


@router.get("/status", response_model=VoiceStatusOut)
async def voice_status(
    auth: AuthContext = Depends(get_auth_context), db: DBSession = Depends(get_db)
) -> VoiceStatusOut:
    tenant = await db.get(Tenant, auth.tenant_id)
    current_slug = tenant.slug if tenant else ""
    matches = bool(settings.voice_tenant_slug and settings.voice_tenant_slug == current_slug)
    deepgram = bool(settings.deepgram_api_key)
    groq = bool(settings.groq_api_key)
    openrouter = bool(settings.openrouter_api_key)
    llm_provider = settings.llm_provider
    llm_model = (
        f"fast={settings.groq_fast_model}; reasoning={settings.groq_reasoning_model}"
        if llm_provider == "groq"
        else settings.openrouter_model
    )
    llm_configured = groq if llm_provider == "groq" else openrouter
    return VoiceStatusOut(
        deepgram_configured=deepgram,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_configured=llm_configured,
        groq_configured=groq,
        openrouter_configured=openrouter,
        configured_tenant_slug=settings.voice_tenant_slug,
        current_tenant_slug=current_slug,
        tenant_matches=matches,
        browser_voice_ready=deepgram and llm_configured and matches,
    )


@router.get("/uat-health")
async def voice_uat_health(
    auth: AuthContext = Depends(get_auth_context), db: DBSession = Depends(get_db)
) -> dict:
    """Authenticated, read-only live evidence for the salon UAT console."""
    voice = await voice_status(auth, db)
    configured_tenant_result = await db.execute(
        select(Tenant).where(Tenant.slug == settings.voice_tenant_slug)
    )
    configured_tenant = configured_tenant_result.scalar_one_or_none()

    metrics: dict[str, float] = {}
    aurora_reachable = False
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{settings.aurora_control_api_url.rstrip('/')}/metrics")
            response.raise_for_status()
        aurora_reachable = True
        for line in response.text.splitlines():
            if not line or line.startswith("#") or " " not in line:
                continue
            name, raw_value = line.rsplit(" ", 1)
            metric_name = name.split("{", 1)[0]
            try:
                metrics[metric_name] = metrics.get(metric_name, 0.0) + float(raw_value)
            except ValueError:
                continue
    except (httpx.HTTPError, OSError):
        aurora_reachable = False

    aurora_up = metrics.get("aurora_up", 0.0) == 1.0
    sip_registered = metrics.get("aurora_sip_registered", 0.0) == 1.0
    rtp_packets = metrics.get("aurora_rtp_packets_total", 0.0)
    rtp_send_errors = metrics.get("aurora_rtp_send_errors_total", 0.0)
    rtp_no_flow = metrics.get("aurora_rtp_no_flow_total", 0.0)

    try:
        pipecat_available = (
            importlib.util.find_spec("pipecat") is not None
            and importlib.util.find_spec("apps.voice.bot") is not None
        )
    except (ImportError, ModuleNotFoundError, ValueError):
        pipecat_available = False

    # The UAT console checks the configured voice-test tenant, not the
    # tenant used to sign into the dashboard. SIP ingress resolves tenants
    # independently; a dashboard admin login must not make global voice
    # configuration appear broken.
    voice_configured = bool(
        voice.deepgram_configured
        and voice.llm_configured
        and configured_tenant is not None
        and settings.aurora_rtp_host
        and settings.aurora_control_api_token
    )
    return {
        "fastapi": {"ready": True},
        "aurora": {
            "reachable": aurora_reachable,
            "service_up": aurora_up,
            "sip_registered": sip_registered,
            "active_calls": int(metrics.get("aurora_calls_active", 0.0)),
            "rtp_packets_forwarded": int(rtp_packets),
            "rtp_send_errors": int(rtp_send_errors),
            "rtp_no_flow_legs": int(rtp_no_flow),
            "callback_errors": int(metrics.get("aurora_callback_requests_total", 0.0))
            if aurora_reachable else None,
        },
        "pipecat": {
            "module_available": pipecat_available,
            "launch_mode": "on-demand per incoming call",
            "ready_for_calls": bool(pipecat_available and voice_configured and aurora_up and sip_registered),
        },
        "voice_configuration": {
            "ready": voice_configured,
            "deepgram_configured": voice.deepgram_configured,
            "llm_provider": voice.llm_provider,
            "llm_configured": voice.llm_configured,
            "tenant_matches": voice.tenant_matches,
            "configured_tenant_exists": configured_tenant is not None,
            "configured_tenant_name": configured_tenant.name if configured_tenant else None,
            "rtp_host_configured": bool(settings.aurora_rtp_host),
            "aurora_control_token_configured": bool(settings.aurora_control_api_token),
        },
    }
