# PRISM LINK — Salon-First MVP

PRISM LINK is a tenant-aware AI receptionist for single-location salons. It answers enquiries,
handles appointment workflows, records messages, and persists call outcomes for the salon dashboard.

## Current phase

The Aurora SIP/RTP integration and Pipecat call pipeline are in the repository. End-to-end telephone
calling is still being validated: recent Aurora event callbacks were rejected with HTTP 422 because
the call ID did not match the API's UUID requirement. Verify event handling, two-way audio, booking
and Google Calendar writes, and call cleanup before forwarding customer calls. See
[the MVP status](docs/MVP_STATUS.md) and [Aurora setup](docs/AURORA_SETUP.md).

## Implemented

- 14-day no-card trial registration and owner login.
- Tenant-scoped salon profile, services, staff, and business hours.
- Tenant-specific Google OAuth with encrypted local credential storage and token refresh.
- Availability, booking, rescheduling, and cancellation with explicit confirmation.
- Idempotent appointment creation, conflict checks, and serialized booking changes.
- Call logs, transcripts, outcomes, summaries, and a salon message inbox.
- Browser/WebRTC voice using Pipecat, Deepgram STT/TTS, and Groq/OpenRouter tool calling.
- Deterministic handling for common booking, message, routing, hours, and confirmation turns.
- Aurora SIP/RTP transport with an isolated Pipecat pipeline per call and called-number tenant routing.
- Dashboard, health endpoints, Alembic migrations, Docker assets, and automated tests.

## Quick start

Requires Python 3.12+. Before starting the API, copy `.env.example` to `.env` and set distinct random values for `JWT_SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEY`, and `OAUTH_STATE_SECRET` (each at least 32 characters). Generate them with `python -c "import secrets; print(secrets.token_urlsafe(48))"`; run it three times and store the values only in the ignored `.env` file. The API refuses to start with unset, placeholder, short, or reused keys outside tests.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
python -m uvicorn apps.api.main:app --reload
```

Open <http://localhost:8000>. The default configuration uses SQLite and the mock calendar, so no
external account is required for dashboard and simulator development. API documentation is at
<http://localhost:8000/docs>.

## Verification

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

The suite covers tenant isolation, booking safety and concurrency, migrations, voice state
handling, messages, Aurora RTP packet/codec handling, and voice-call isolation.

## Voice and Aurora

Install the voice stack:

```powershell
pip install -e ".[dev,voice]"
```

Run browser/WebRTC voice:

```powershell
python -m apps.voice.bot --transport webrtc
```

The API starts the Aurora call pipeline from SIP events. Configure the private API environment and
Aurora callback/control API using [Aurora integration setup](docs/AURORA_SETUP.md). Keep provider
keys and shared control tokens in the ignored `.env` file.

## Database

SQLite remains the zero-setup default for lightweight local development and tests. For a local
PostgreSQL instance, the repository provides a Compose service. Docker Compose reads the private
`.env` file for the PostgreSQL credentials:

1. Copy `.env.example` to `.env` if you have not already done so.
2. Set `POSTGRES_PASSWORD` in `.env` to a strong local development password. Keep this file
   private; never commit it.
3. Start PostgreSQL and Redis:

```bash
docker compose up -d postgres redis
docker compose ps
```

The Compose database is named `prism_link` and its default role is `prism_link_app`. The
PostgreSQL port is bound to `127.0.0.1`, not exposed on the LAN. Configure the API's
`DATABASE_URL` in `.env` to use the same username and password:

```dotenv
DATABASE_URL=postgresql+psycopg://prism_link_app:URL_ENCODED_PASSWORD@127.0.0.1:5432/prism_link
```

Replace `URL_ENCODED_PASSWORD` with the URL-encoded value of `POSTGRES_PASSWORD`. URL-encode
reserved characters in the password (for example, `@`, `/`, `:`, and `#`). Do not put
literal placeholder text in the actual connection string.

Apply the repository's Alembic migrations before running against PostgreSQL:

```bash
python -m alembic upgrade head
python -m alembic current
```

Then start or restart the API. To return to the default SQLite development setup, set
`DATABASE_URL=sqlite:///./prism_link.db` and restart the API.

**Existing Compose volumes:** PostgreSQL only applies `POSTGRES_USER`, `POSTGRES_DB`, and
`POSTGRES_PASSWORD` when it initializes an empty data directory. Changing these values does not
rename or update roles in an existing `prism_postgres` volume. Do not delete the volume to resolve
an authentication issue; inspect and migrate the existing database deliberately.

Development startup creates missing tables for convenience. Production startup deliberately
requires migrations and will not create tables automatically. Redis is provisioned for the live
call coordination boundary but is not yet the authoritative session coordinator.

## Documentation

- [Documentation index](docs/README.md)
- [Current MVP and pilot status](docs/MVP_STATUS.md)
- [Voice runtime setup](docs/VOICE_SETUP.md)
- [Google Calendar OAuth setup](docs/GOOGLE_OAUTH_SETUP.md)
- [OpenRouter development setup](docs/OPENROUTER_SETUP.md)

## Project layout

```text
apps/api/          FastAPI application and HTTP routes
apps/dashboard/    Salon dashboard
apps/voice/        Browser voice runtime
core/              Settings, security, and validation
database/          Models, repositories, and sessions
docs/              Current guides and historical implementation notes
migrations/        Alembic schema migrations
services/          Booking, calendar, credentials, and voice domain logic
tests/             Automated behavior and regression tests
```

## Pilot boundary

Before a salon pilot, complete controlled real-phone tests for booking, rescheduling,
cancellation, messages, after-hours handling, interruption, hang-up, and provider failure. Replace
the local credential vault with managed secret storage, add monitoring and alerting, and agree the
recording/transcript consent and retention policy.

Outlook, CRM integrations, multi-location support, advanced analytics, outbound campaigns,
multilingual support, and cross-industry customization remain outside the salon-first MVP.
