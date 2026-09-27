from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx

from core.config import get_settings
from database.repositories import CalendarConnectionRepository
from database.session import DBSession
from database.utils import utcnow
from services.calendar.base import CalendarEvent
from services.calendar.google_oauth import GoogleOAuthError, refresh_access_token
from services.credentials import CredentialStoreError, LocalEncryptedCredentialStore


class GoogleCalendarProviderError(RuntimeError):
    pass


class GoogleCalendarProvider:
    """Tenant-aware Google Calendar REST adapter with automatic token refresh."""

    base_url = "https://www.googleapis.com/calendar/v3"

    def __init__(self, db: DBSession, tenant_id: UUID) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.settings = get_settings()
        self.connections = CalendarConnectionRepository(db, tenant_id)
        self.store = LocalEncryptedCredentialStore()

    async def _auth(self) -> tuple[str, str]:
        connection = await self.connections.active()
        calendar_id = self.settings.google_calendar_id

        if connection is not None:
            calendar_id = connection.external_calendar_id or "primary"
            credential_ref = connection.credential_ref or ""
            if credential_ref.startswith(LocalEncryptedCredentialStore.prefix):
                try:
                    credentials = self.store.get(credential_ref)
                except CredentialStoreError as exc:
                    raise GoogleCalendarProviderError(str(exc)) from exc
                credentials = await self._refresh_if_needed(connection, credentials)
                token = credentials.get("access_token")
                if token:
                    return str(token), calendar_id
                raise GoogleCalendarProviderError("Stored Google access token is missing")
            if credential_ref.startswith("env:") and self.settings.google_calendar_access_token:
                return self.settings.google_calendar_access_token, calendar_id

        # Backward-compatible local development path during OAuth migration.
        if self.settings.google_calendar_access_token:
            return self.settings.google_calendar_access_token, calendar_id

        raise GoogleCalendarProviderError(
            "Google Calendar is not connected for this salon. Connect Google Calendar in Salon setup."
        )

    async def _refresh_if_needed(self, connection, credentials: dict) -> dict:
        expires_raw = credentials.get("expires_at")
        if not expires_raw:
            return credentials
        try:
            expires_at = datetime.fromisoformat(str(expires_raw).replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
        except ValueError:
            expires_at = datetime.now(UTC) - timedelta(seconds=1)
        if expires_at > datetime.now(UTC) + timedelta(seconds=60):
            return credentials

        refresh_token = credentials.get("refresh_token")
        if not refresh_token:
            raise GoogleCalendarProviderError(
                "Google Calendar authorization expired and no refresh token is available. Reconnect Google Calendar."
            )
        try:
            refreshed = await refresh_access_token(str(refresh_token))
        except GoogleOAuthError as exc:
            raise GoogleCalendarProviderError(
                "Google Calendar authorization could not be refreshed. Reconnect Google Calendar."
            ) from exc

        # Google normally omits refresh_token during refresh. Preserve the existing one.
        refreshed["refresh_token"] = refresh_token
        if "scope" not in refreshed and credentials.get("scope"):
            refreshed["scope"] = credentials["scope"]
        connection.credential_ref = self.store.put(self.tenant_id, refreshed)
        connection.last_verified_at = utcnow()
        connection.updated_at = utcnow()
        await self.db.commit()
        return refreshed

    async def _headers(self) -> tuple[dict[str, str], str]:
        token, calendar_id = await self._auth()
        return (
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            calendar_id,
        )

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {401, 403}:
                raise GoogleCalendarProviderError(
                    "Google Calendar authorization was rejected. Reconnect Google Calendar."
                ) from exc
            raise GoogleCalendarProviderError(
                f"Google Calendar request failed ({status_code})."
            ) from exc
        except httpx.HTTPError as exc:
            raise GoogleCalendarProviderError(
                "Google Calendar is temporarily unavailable."
            ) from exc

    async def is_available(
        self,
        *,
        tenant_id: UUID,
        starts_at: datetime,
        ends_at: datetime,
        staff_member_id: UUID | None,
        exclude_appointment_id: UUID | None = None,
    ) -> bool:
        headers, calendar_id = await self._headers()
        body = {
            "timeMin": starts_at.isoformat(),
            "timeMax": ends_at.isoformat(),
            "items": [{"id": calendar_id}],
        }
        response = await self._request(
            "POST",
            f"{self.base_url}/freeBusy",
            headers=headers,
            json=body,
        )
        busy = response.json().get("calendars", {}).get(calendar_id, {}).get("busy", [])
        return not busy

    async def create_event(
        self,
        *,
        tenant_id: UUID,
        summary: str,
        starts_at: datetime,
        ends_at: datetime,
        description: str,
    ) -> CalendarEvent:
        headers, calendar_id = await self._headers()
        body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": starts_at.isoformat()},
            "end": {"dateTime": ends_at.isoformat()},
        }
        response = await self._request(
            "POST",
            f"{self.base_url}/calendars/{calendar_id}/events",
            headers=headers,
            json=body,
        )
        event = response.json()
        return CalendarEvent(event["id"], starts_at, ends_at)

    async def update_event(
        self,
        *,
        external_event_id: str,
        summary: str,
        starts_at: datetime,
        ends_at: datetime,
        description: str,
    ) -> CalendarEvent:
        headers, calendar_id = await self._headers()
        body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": starts_at.isoformat()},
            "end": {"dateTime": ends_at.isoformat()},
        }
        await self._request(
            "PATCH",
            f"{self.base_url}/calendars/{calendar_id}/events/{external_event_id}",
            headers=headers,
            json=body,
        )
        return CalendarEvent(external_event_id, starts_at, ends_at)

    async def cancel_event(self, *, external_event_id: str) -> None:
        headers, calendar_id = await self._headers()
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.delete(
                    f"{self.base_url}/calendars/{calendar_id}/events/{external_event_id}",
                    headers=headers,
                )
            if response.status_code in {204, 410}:
                return
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise GoogleCalendarProviderError(
                    "Google Calendar authorization was rejected. Reconnect Google Calendar."
                ) from exc
            raise GoogleCalendarProviderError(
                f"Google Calendar request failed ({exc.response.status_code})."
            ) from exc
        except httpx.HTTPError as exc:
            raise GoogleCalendarProviderError(
                "Google Calendar is temporarily unavailable."
            ) from exc
