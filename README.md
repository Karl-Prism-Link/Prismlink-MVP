# PRISM LINK — Salon-First MVP

PRISM LINK is a tenant-aware AI receptionist for single-location salons. It answers enquiries,
handles appointment workflows, records messages, and persists call outcomes for the salon dashboard.

## Current phase

The software MVP is runnable, and the first real end-to-end telephone call through FreeSWITCH and
the Pipecat voice runtime has passed:

```text
Phone / SIP carrier
        ↓
FreeSWITCH
        ↓ bidirectional PCM WebSocket
PRISM LINK voice runtime
        ↓
Deepgram STT/TTS + Groq or OpenRouter
        ↓
Tenant-scoped booking tools + Google Calendar
```

The deployed 2talk trunk, FreeSWITCH build, codec path, media WebSocket, Deepgram STT/TTS, Groq, and
audible telephone playback have been verified on an initial live call. Repeatable booking and
failure-path scenarios, cleanup hardening, monitoring, privacy controls, and pilot operations are
still required. Do not forward customer calls until the real-phone exit criteria in
[the MVP status](docs/MVP_STATUS.md) pass.

## Implemented

- 14-day no-card trial registration and owner login.
- Tenant-scoped salon profile, services, staff, and business hours.
- Tenant-specific Google OAuth with encrypted local credential storage and token refresh.
- Availability, booking, rescheduling, and cancellation with explicit confirmation.
- Idempotent appointment creation, conflict checks, and serialized booking changes.
- Call logs, transcripts, outcomes, summaries, and a salon message inbox.
- Browser/WebRTC voice using Pipecat, Deepgram STT/TTS, and Groq/OpenRouter tool calling.
- Deterministic handling for common booking, message, routing, hours, and confirmation turns.
- FreeSWITCH media WebSocket transport with per-call isolation and DID-based tenant routing.
- Dashboard, health endpoints, Alembic migrations, Docker assets, and automated tests.

## Quick start

Requires Python 3.12+.

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
handling, messages, FreeSWITCH transport isolation, and Aurora RTP packet/codec helpers.

## Voice and FreeSWITCH

Install the voice stack:

```powershell
pip install -e ".[dev,voice]"
```

Run browser/WebRTC voice:

```powershell
python -m apps.voice.bot --transport webrtc
```

Run the FreeSWITCH listener:

```powershell
python -m apps.voice.bot_freeswitch
```

Provider keys and transport settings belong only in the ignored `.env` file. Follow the
[voice and FreeSWITCH setup guide](docs/VOICE_SETUP.md) for configuration and live-call checks.
For the Aurora SIP/RTP edge, follow [Aurora integration setup](docs/AURORA_SETUP.md).

## Database

Local PostgreSQL and Redis are available through Compose:

```powershell
docker compose up -d postgres redis
```

Configure PostgreSQL in `.env`, then apply migrations:

```powershell
python -m alembic upgrade head
```

Development startup creates missing tables for convenience. Production startup deliberately
requires migrations and will not create tables automatically. Redis is provisioned for the live
call coordination boundary but is not yet the authoritative session coordinator.

## Documentation

- [Documentation index](docs/README.md)
- [Current MVP and pilot status](docs/MVP_STATUS.md)
- [Voice and FreeSWITCH setup](docs/VOICE_SETUP.md)
- [Google Calendar OAuth setup](docs/GOOGLE_OAUTH_SETUP.md)
- [OpenRouter development setup](docs/OPENROUTER_SETUP.md)

## Project layout

```text
apps/api/          FastAPI application and HTTP routes
apps/dashboard/    Salon dashboard
apps/voice/        Browser and FreeSWITCH voice entry points
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
