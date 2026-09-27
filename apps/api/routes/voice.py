from fastapi import APIRouter, Depends

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
