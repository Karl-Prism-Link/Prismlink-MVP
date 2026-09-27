# PRISM LINK MVP status

Last updated: 20 September 2026

## Summary

PRISM LINK has a working salon-first software MVP and has completed its first end-to-end telephone
call through 2talk, FreeSWITCH, Deepgram, and Groq. The active milestone is repeatable real-phone
scenario validation and pre-pilot operational hardening. Broad customer-call proof and production
operations remain incomplete.

## Capability status

| Capability | Status | Notes |
|---|---|---|
| Trial and owner authentication | Implemented | Registration creates a tenant and 14-day trial. Production settings require independent strong secrets. |
| Tenant isolation | Implemented and tested | Protected data access is scoped to the authenticated tenant. |
| Salon setup | Implemented | Profile, services, staff, phone number, timezone, greeting, and business hours. |
| Google Calendar | Implemented for development/pilot | Per-tenant OAuth, encrypted local credential references, refresh, disconnect, and revoke. Use managed secret storage before production. |
| Appointment workflows | Implemented and tested | Availability, booking, rescheduling, and cancellation require explicit confirmation and enforce business rules. |
| Booking concurrency | Implemented and tested | Idempotency, conflict checks, and serialized writes prevent duplicate concurrent booking. |
| Calls and messages | Implemented | Call logs, transcripts, summaries, outcomes, and tenant-scoped follow-up messages. |
| Dashboard | Implemented | Salon setup, calendar status, bookings, calls, summaries, and message workflow. |
| Browser/WebRTC voice | Implemented | Pipecat with Deepgram STT/TTS and Groq/OpenRouter tool calling. |
| FreeSWITCH transport | Connected; initial live call passed | 2talk trunking, FreeSWITCH, bidirectional PCM streaming, tenant routing, Deepgram, Groq, and audible TTS were verified. Full scenario and failure-path validation remains. |
| Database migrations | Implemented | Alembic owns production schema changes; production startup does not auto-create tables. |
| Redis call coordination | Planned | Redis is provisioned but is not yet the authoritative session/retry coordinator. |
| Monitoring and alerts | Planned | Correlation IDs exist; structured operational metrics, alerting, and reconciliation are still required. |
| Recording and transcript policy | Planned | Consent, access, retention, deletion, and New Zealand privacy review are required before production recording. |
| Usage and cost events | Planned | Provider quantities and cost reporting are not yet surfaced. |

## What is currently runnable

A developer can run the dashboard and call simulator without external providers. With provider
credentials, the browser microphone path runs real streaming STT, tool-constrained conversation,
TTS, Google Calendar actions, and call persistence.

The FreeSWITCH listener creates one isolated PRISM LINK runtime per media WebSocket connection and
routes a call by `called_number`, with `tenant_slug` available as a development fallback. The first
deployed end-to-end call passed, including STT, LLM processing, TTS, and telephone playback. The
integration is not considered pilot-proven until the remaining deployed-system checks below pass
repeatedly.

## FreeSWITCH integration exit criteria

- A real inbound test call reaches the listener through the intended SIP/carrier route.
- Incoming speech is intelligible and outgoing TTS is audible without sustained clipping,
  distortion, or material buffering delay.
- Called-number routing selects the correct salon; unknown numbers fail closed.
- Caller number and external call ID are captured correctly.
- Booking, rescheduling, cancellation, enquiry, message, and after-hours scenarios succeed.
- Caller interruption, silence, hang-up during speech, WebSocket loss, and provider failures clean
  up safely and produce diagnosable logs.
- Concurrent calls remain isolated and respect the configured capacity limit.
- Calls, transcripts, outcomes, summaries, messages, and appointment references appear under the
  correct tenant.

## Controlled pilot exit criteria

- Complete the FreeSWITCH criteria above using the pilot deployment and test DID.
- Add structured logs, failure alerts, retry/reconciliation behavior, and an operator runbook.
- Replace local OAuth credential storage with a managed secrets service.
- Define recording/transcript consent, access, retention, export, and deletion behavior.
- Run a small scenario pack repeatedly before forwarding a salon's public number.
- Obtain explicit pilot approval from the salon and keep a rapid rollback/forwarding path.

## Intentionally deferred

Outlook/Microsoft 365, CRM integrations, multi-location support, advanced staff optimization, deep
analytics, outbound campaigns, multilingual support, and cross-industry customization are outside
the current salon-first MVP.

Detailed implementation notes from earlier increments are retained in [`docs/history`](history/).
