from __future__ import annotations

from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from urllib.parse import urlencode
from uuid import UUID

import httpx
import jwt
from fastapi import HTTPException, status

from core.config import get_settings

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_SCOPES = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
)


class GoogleOAuthError(RuntimeError):
    pass


def _require_client_settings() -> tuple[str, str, str]:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "GOOGLE_OAUTH_NOT_CONFIGURED",
                "message": "Google OAuth client ID and secret are not configured.",
            },
        )
    return (
        settings.google_client_id,
        settings.google_client_secret,
        settings.google_oauth_redirect_uri_resolved,
    )


def create_oauth_state(
    *, tenant_id: UUID, user_id: UUID, external_calendar_id: str = "primary"
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "purpose": "google_calendar_oauth",
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "external_calendar_id": external_calendar_id,
        "nonce": token_urlsafe(24),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=10)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")


def decode_oauth_state(state_token: str) -> dict:
    try:
        payload = jwt.decode(
            state_token,
            get_settings().jwt_secret_key,
            algorithms=["HS256"],
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_OAUTH_STATE", "message": "Google connection expired."},
        ) from exc
    if payload.get("purpose") != "google_calendar_oauth":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_OAUTH_STATE",
                "message": "Google connection state is invalid.",
            },
        )
    return payload


def build_authorization_url(
    *, tenant_id: UUID, user_id: UUID, external_calendar_id: str = "primary"
) -> str:
    client_id, _client_secret, redirect_uri = _require_client_settings()
    state = create_oauth_state(
        tenant_id=tenant_id,
        user_id=user_id,
        external_calendar_id=external_calendar_id,
    )
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    client_id, client_secret, redirect_uri = _require_client_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if response.is_error:
        raise GoogleOAuthError(f"Google token exchange failed ({response.status_code})")
    tokens = response.json()
    if not tokens.get("access_token"):
        raise GoogleOAuthError("Google token exchange did not return an access token")
    expires_in = int(tokens.get("expires_in", 3600))
    tokens["expires_at"] = (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat()
    return tokens


async def refresh_access_token(refresh_token: str) -> dict:
    client_id, client_secret, _redirect_uri = _require_client_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if response.is_error:
        raise GoogleOAuthError(f"Google token refresh failed ({response.status_code})")
    tokens = response.json()
    if not tokens.get("access_token"):
        raise GoogleOAuthError("Google token refresh did not return an access token")
    expires_in = int(tokens.get("expires_in", 3600))
    tokens["expires_at"] = (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat()
    return tokens


async def fetch_account_email(access_token: str) -> str | None:
    async with httpx.AsyncClient(timeout=8.0) as client:
        response = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.is_error:
        return None
    email = response.json().get("email")
    return email if isinstance(email, str) else None


async def revoke_token(token: str) -> None:
    async with httpx.AsyncClient(timeout=8.0) as client:
        response = await client.post(
            GOOGLE_REVOKE_URL,
            params={"token": token},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code not in {200, 400}:
        response.raise_for_status()
