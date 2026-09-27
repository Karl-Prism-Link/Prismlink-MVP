# PRISM LINK — Google Calendar OAuth local setup

This replaces the temporary developer access-token method with a tenant-specific Google OAuth connection.

## 1. Google Cloud setup

1. Create or select a Google Cloud project used only for PRISM LINK development.
2. Enable the Google Calendar API.
3. Configure the OAuth consent/branding screen.
4. Create an OAuth 2.0 client of type **Web application**.
5. Add this exact Authorized redirect URI for local development:

```text
http://localhost:8000/api/v1/calendar/google/callback
```

6. Copy the generated client ID and client secret into `.env`. Do not commit them.

## 2. Local environment

Set:

```env
CALENDAR_PROVIDER=google
GOOGLE_CLIENT_ID=<client-id>
GOOGLE_CLIENT_SECRET=<client-secret>
GOOGLE_OAUTH_REDIRECT_URI=http://localhost:8000/api/v1/calendar/google/callback
CREDENTIAL_ENCRYPTION_KEY=<a-long-random-secret-different-from-JWT_SECRET_KEY>
CREDENTIAL_STORE_PATH=.secrets
```

Leave the old developer-token value blank once OAuth works:

```env
GOOGLE_CALENDAR_ACCESS_TOKEN=
```

After changing dependencies/configuration:

```powershell
pip install -e ".[dev]"
python -m uvicorn apps.api.main:app --reload
```

## 3. Connect a salon

1. Sign in to `http://localhost:8000`.
2. Open **Salon setup**.
3. Leave Calendar ID as `primary` unless the salon intentionally uses a different Google calendar ID.
4. Select **Connect Google Calendar**.
5. Sign in to the dedicated development Google account and approve the Calendar permissions.
6. Google redirects back to PRISM LINK. The dashboard should show the connected Google account and calendar ID.

PRISM LINK requests offline access so it can refresh access tokens when the salon owner is not present. The requested calendar scopes are:

```text
https://www.googleapis.com/auth/calendar.events
https://www.googleapis.com/auth/calendar.freebusy
```

It also requests `openid email` so the dashboard can label the connected account.

## 4. Credential storage behavior

- `calendar_connections.credential_ref` stores only a reference such as `local-google:<tenant UUID>`.
- OAuth tokens are encrypted in `.secrets/google/`.
- The encryption key comes from `CREDENTIAL_ENCRYPTION_KEY` (or falls back to `JWT_SECRET_KEY` for development compatibility).
- Access tokens are refreshed automatically using the stored refresh token.
- Disconnect attempts Google token revocation, deletes the local encrypted token file, and marks the tenant connection disconnected.
- `.secrets/` is git-ignored.

For production, replace `LocalEncryptedCredentialStore` with a managed secret store/KMS integration. Do not place OAuth tokens in browser localStorage or plaintext application-table columns.

## 5. Verification

After connecting through OAuth, run this sequence with the call simulator:

1. Book an appointment in a clearly free slot.
2. Verify the event appears in Google Calendar and PRISM LINK records a non-`mock_` `external_event_id`.
3. Reschedule the same appointment and verify the existing Google event moves rather than duplicating.
4. Cancel it and verify the event is removed/cancelled in Google Calendar and the local appointment status is `cancelled`.
5. Restart PRISM LINK and repeat an availability/booking test. This checks that the persistent refresh-token path works rather than relying on the browser session.
6. Use **Disconnect** and confirm subsequent booking attempts fail safely with a calendar-unavailable/follow-up outcome rather than claiming success.

## 6. Current security boundary

The OAuth flow uses a signed, short-lived `state` token to bind the Google callback to the PRISM LINK tenant/user and to protect the callback from an unrelated authorization response. For a larger production deployment, add one-time state replay tracking and move encrypted credential material into managed secrets infrastructure.
