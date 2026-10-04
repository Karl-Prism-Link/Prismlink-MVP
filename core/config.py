from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "development-only-change-me"


class Settings(BaseSettings):
    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./prism_link.db"
    jwt_secret_key: str = DEFAULT_JWT_SECRET
    jwt_expire_minutes: int = 60
    public_base_url: str = "http://localhost:8000"
    calendar_provider: Literal["mock", "google"] = "mock"

    # Legacy single-token development path. OAuth connections take precedence.
    google_calendar_access_token: str | None = None
    google_calendar_id: str = "primary"

    # Per-tenant Google OAuth settings.
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_oauth_redirect_uri: str | None = None
    credential_encryption_key: str | None = None
    credential_store_path: str = ".secrets"

    deepgram_api_key: str | None = None

    # Voice LLM provider. Groq remains the architecture default; OpenRouter is
    # available as a development/testing provider so low/no-cost models can be used.
    llm_provider: Literal["groq", "openrouter"] = "groq"
    groq_api_key: str | None = None
    openrouter_api_key: str | None = None

    # Live voice development runtime. VOICE_TENANT_SLUG selects a salon for
    # browser/WebRTC testing; SIP ingress will resolve tenants by called number.
    voice_tenant_slug: str | None = None
    voice_test_caller_number: str = "0210000000"
    # v0.4 routed voice architecture. GROQ_MODEL remains as a legacy single-model
    # override, while FAST/REASONING models can be tuned independently.
    groq_model: str = "openai/gpt-oss-20b"
    groq_fast_model: str = "openai/gpt-oss-20b"
    groq_reasoning_model: str = "openai/gpt-oss-120b"
    groq_max_completion_tokens: int = 256
    groq_max_retries: int = 1
    groq_timeout_seconds: float = 10.0
    groq_reasoning_effort: Literal["low", "medium", "high"] = "low"
    voice_context_turns: int = 4
    openrouter_model: str = "openrouter/free"
    deepgram_stt_model: str = "nova-3-general"
    deepgram_stt_language: str = "en-NZ"
    deepgram_tts_voice: str = "aura-2-helena-en"

    redis_url: str = "redis://localhost:6379/0"
    record_calls: bool = False

    # Local Aurora SIP control and RTP bridge. Keep the webhook/control API on
    # loopback; AURORA_RTP_HOST is Aurora's configured local_ip (not external_ip).
    aurora_control_api_url: str = "http://127.0.0.1:8088"
    aurora_control_api_token: str | None = None
    # Aurora's local_ip is the RTP destination; bind the Pipecat UDP socket to loopback.
    aurora_rtp_host: str | None = None
    aurora_rtp_bind_host: str = "127.0.0.1"
    aurora_startup_timeout_seconds: float = 20.0
    aurora_max_active_calls: int = 8

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def google_oauth_redirect_uri_resolved(self) -> str:
        if self.google_oauth_redirect_uri:
            return self.google_oauth_redirect_uri
        return f"{self.public_base_url.rstrip('/')}/api/v1/calendar/google/callback"


def validate_runtime_settings(settings: Settings) -> None:
    """Fail closed when a production deployment has development-grade secrets."""
    if settings.app_env != "production":
        return
    errors: list[str] = []
    if settings.jwt_secret_key == DEFAULT_JWT_SECRET or len(settings.jwt_secret_key) < 32:
        errors.append("JWT_SECRET_KEY must be a unique value of at least 32 characters")
    if not settings.credential_encryption_key or len(settings.credential_encryption_key) < 32:
        errors.append(
            "CREDENTIAL_ENCRYPTION_KEY must be a separate value of at least 32 characters"
        )
    if errors:
        raise RuntimeError("Invalid production configuration: " + "; ".join(errors))


@lru_cache
def get_settings() -> Settings:
    return Settings()
