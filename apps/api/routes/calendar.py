from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from apps.api.dependencies import AuthContext, get_auth_context
from apps.api.schemas import CalendarConnectIn, CalendarStatusOut, GoogleOAuthStartOut
from core.config import get_settings
from database.models import AuditLog, CalendarConnection, User
from database.repositories import CalendarConnectionRepository
from database.session import DBSession, get_db
from database.utils import utcnow
from services.calendar.google_oauth import (
    GoogleOAuthError,
    build_authorization_url,
    decode_oauth_state,
    exchange_code,
    fetch_account_email,
    revoke_token,
)
from services.credentials import CredentialStoreError, LocalEncryptedCredentialStore

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])


@router.get("/status", response_model=CalendarStatusOut | None)
async def calendar_status(
    auth: AuthContext = Depends(get_auth_context), db: DBSession = Depends(get_db)
):
    return await CalendarConnectionRepository(db, auth.tenant_id).active()


@router.get("/google/connect", response_model=GoogleOAuthStartOut)
async def start_google_oauth(
    external_calendar_id: str = Query(default="primary", min_length=1, max_length=256),
    auth: AuthContext = Depends(get_auth_context),
) -> GoogleOAuthStartOut:
    authorization_url = build_authorization_url(
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        external_calendar_id=external_calendar_id,
    )
    return GoogleOAuthStartOut(authorization_url=authorization_url)


@router.get("/google/callback", include_in_schema=False)
async def google_oauth_callback(
    code: str | None = None,
    state_token: str | None = Query(default=None, alias="state"),
    error: str | None = None,
    db: DBSession = Depends(get_db),
):
    if error or not code or not state_token:
        return RedirectResponse(url="/?calendar=error", status_code=303)

    try:
        state_payload = decode_oauth_state(state_token)
        tenant_id = UUID(state_payload["tenant_id"])
        user_id = UUID(state_payload["user_id"])
        external_calendar_id = str(state_payload.get("external_calendar_id") or "primary")[:256]
    except (KeyError, ValueError, HTTPException):
        return RedirectResponse(url="/?calendar=error", status_code=303)

    user = await db.get(User, user_id)
    if user is None or not user.is_active or user.tenant_id != tenant_id:
        return RedirectResponse(url="/?calendar=error", status_code=303)

    repo = CalendarConnectionRepository(db, tenant_id)
    existing = await repo.latest()
    store = LocalEncryptedCredentialStore()

    try:
        tokens = await exchange_code(code)
        granted_scopes = set(str(tokens.get("scope", "")).split())
        required_calendar_scopes = {
            "https://www.googleapis.com/auth/calendar.events",
            "https://www.googleapis.com/auth/calendar.freebusy",
        }
        if not required_calendar_scopes.issubset(granted_scopes):
            raise GoogleOAuthError("Required Calendar permissions were not granted")

        # Re-consent normally returns a refresh token. Preserve the prior token if Google omits it.
        if not tokens.get("refresh_token") and existing and existing.credential_ref:
            try:
                prior = store.get(existing.credential_ref)
                if prior.get("refresh_token"):
                    tokens["refresh_token"] = prior["refresh_token"]
            except CredentialStoreError:
                pass

        if not tokens.get("refresh_token"):
            raise GoogleOAuthError("Google did not return an offline refresh token")

        account_email = await fetch_account_email(str(tokens["access_token"]))
        credential_ref = store.put(tenant_id, tokens)
    except (GoogleOAuthError, CredentialStoreError, HTTPException):
        return RedirectResponse(url="/?calendar=error", status_code=303)

    now = utcnow()
    if existing is None:
        existing = CalendarConnection(
            tenant_id=tenant_id,
            provider="google",
            account_label=account_email,
            external_calendar_id=external_calendar_id,
            status="connected",
            credential_ref=credential_ref,
            connected_at=now,
            last_verified_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(existing)
    else:
        existing.provider = "google"
        existing.account_label = account_email
        existing.external_calendar_id = external_calendar_id
        existing.status = "connected"
        existing.credential_ref = credential_ref
        existing.connected_at = now
        existing.last_verified_at = now
        existing.updated_at = now

    await db.flush()
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            user_id=user_id,
            action="calendar.google_connected",
            resource_type="calendar_connection",
            resource_id=str(existing.id),
            metadata_json=None,
            created_at=now,
        )
    )
    await db.commit()
    return RedirectResponse(url="/?calendar=connected", status_code=303)


@router.post("/disconnect", status_code=204)
async def disconnect_calendar(
    auth: AuthContext = Depends(get_auth_context),
    db: DBSession = Depends(get_db),
) -> None:
    repo = CalendarConnectionRepository(db, auth.tenant_id)
    connection = await repo.active()
    if connection is None:
        return None

    store = LocalEncryptedCredentialStore()
    if connection.credential_ref and connection.credential_ref.startswith(store.prefix):
        try:
            credentials = store.get(connection.credential_ref)
            token = credentials.get("refresh_token") or credentials.get("access_token")
            if token:
                try:
                    await revoke_token(str(token))
                except Exception:
                    # Disconnect remains local even when Google revocation is temporarily unavailable.
                    pass
        except CredentialStoreError:
            pass
        store.delete(connection.credential_ref)

    now = utcnow()
    connection.status = "disconnected"
    connection.credential_ref = None
    connection.last_verified_at = now
    connection.updated_at = now
    db.add(
        AuditLog(
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            action="calendar.google_disconnected",
            resource_type="calendar_connection",
            resource_id=str(connection.id),
            metadata_json=None,
            created_at=now,
        )
    )
    await db.commit()
    return None


@router.post("/connect", response_model=CalendarStatusOut)
async def connect_calendar_legacy(
    payload: CalendarConnectIn,
    auth: AuthContext = Depends(get_auth_context),
    db: DBSession = Depends(get_db),
) -> CalendarConnection:
    """Legacy development-only metadata path for the temporary .env access token."""
    settings = get_settings()
    if settings.app_env != "development" or not settings.google_calendar_access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "USE_GOOGLE_OAUTH",
                "message": "Use the Google OAuth connection flow instead.",
            },
        )

    now = utcnow()
    repo = CalendarConnectionRepository(db, auth.tenant_id)
    row = await repo.latest()
    if row is None:
        row = CalendarConnection(
            tenant_id=auth.tenant_id,
            provider="google",
            account_label=payload.account_label,
            external_calendar_id=payload.external_calendar_id,
            status="connected",
            credential_ref="env:GOOGLE_CALENDAR_ACCESS_TOKEN",
            connected_at=now,
            last_verified_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.account_label = payload.account_label
        row.external_calendar_id = payload.external_calendar_id
        row.status = "connected"
        row.credential_ref = "env:GOOGLE_CALENDAR_ACCESS_TOKEN"
        row.connected_at = now
        row.last_verified_at = now
        row.updated_at = now
    await db.commit()
    await db.refresh(row)
    return row
