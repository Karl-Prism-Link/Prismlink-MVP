# PRISM LINK MVP status

Last updated: 5 October 2026

## Summary

PRISM LINK's salon-first application, dashboard, booking workflows, and Google Calendar
integration are implemented. Aurora SIP/RTP integration is present in the repository, but inbound
telephone calling has not yet passed end-to-end validation. Recent Aurora callbacks received HTTP
422 because the supplied call ID did not satisfy the API's UUID validation. Calendar booking through
a real telephone call, two-way audio, and call cleanup still need verification.

## Capability status

| Capability | Status | Notes |
|---|---|---|
| Trial and owner authentication | Implemented | Registration creates a tenant and 14-day trial. Production settings require independent strong secrets. |
| Tenant isolation | Implemented and tested | Protected data access is scoped to the authenticated tenant. |
| Salon setup | Implemented | Profile, services, staff, phone number, timezone, greeting, and business hours. |
| Google Calendar | Implemented; live booking needs verification | Per-tenant OAuth, encrypted local credential references, refresh, disconnect, and revoke. Verify a real booking appears in Google Calendar. |
| Appointment workflows | Implemented and tested | Availability, booking, rescheduling, and cancellation require explicit confirmation and enforce business rules. |
| Booking concurrency | Implemented and tested | Idempotency, conflict checks, and serialized writes prevent duplicate concurrent booking. |
| Calls and messages | Implemented | Call logs, transcripts, summaries, outcomes, and tenant-scoped follow-up messages. |
| Dashboard | Implemented | Salon setup, calendar status, bookings, calls, summaries, and message workflow. |
| Browser/WebRTC voice | Implemented | Pipecat with Deepgram STT/TTS and Groq/OpenRouter tool calling. |
| Aurora SIP/RTP integration | Implemented; end-to-end validation in progress | API callback currently rejects a call event with HTTP 422 when its call ID is not a UUID. Verify the Aurora event contract, two-way PCMA/PCMU audio, booking, and teardown. |
| Database migrations | Implemented | Alembic owns production schema changes; production startup does not auto-create tables. |
| Redis call coordination | Planned | Redis is provisioned but is not yet the authoritative session/retry coordinator. |
| Monitoring and alerts | In progress | Health routes and monitoring metrics are present; operational alerts and reconciliation remain. |
| Recording and transcript policy | Planned | Consent, access, retention, deletion, and New Zealand privacy review are required before production recording. |
| Usage and cost events | Planned | Provider quantities and cost reporting are not yet surfaced. |

## What is currently runnable

A developer can run the dashboard and call simulator without external providers. With provider
credentials, the browser microphone path runs real streaming speech recognition, tool-constrained
conversation, speech synthesis, and call persistence.

For telephone calls, Aurora posts SIP events to the PRISM LINK API, which allocates an RTP socket
and starts a per-call Pipecat pipeline. The integration is not pilot-proven until Aurora events are
accepted, two-way audio works, a confirmed booking appears in Google Calendar, and call resources
are reliably released.

## Aurora integration exit criteria

- A real inbound test call reaches the PRISM LINK API through Aurora and the 2talk trunk.
- Aurora event payloads satisfy API validation and call IDs map consistently to the call lifecycle.
- Incoming speech is intelligible and outgoing audio is audible using supported PCMA/PCMU RTP.
- Called-number routing selects the correct salon; unknown numbers fail closed.
- Caller number and external call ID are captured correctly.
- Enquiry, booking, rescheduling, cancellation, message, and after-hours scenarios succeed.
- Confirmed bookings appear once in both PRISM LINK and Google Calendar.
- Caller interruption, silence, hang-up, transport loss, and provider failures clean up safely and
  produce diagnosable logs.
- Concurrent calls remain isolated and respect the configured capacity limit.

## Controlled pilot exit criteria

- Complete the Aurora criteria above repeatedly using the pilot deployment and test number.
- Add structured logs, failure alerts, retry/reconciliation behavior, and an operator runbook.
- Replace local OAuth credential storage with a managed secrets service.
- Define recording/transcript consent, access, retention, export, and deletion behavior.
- Run a small scenario pack before forwarding a salon's public number.
- Obtain explicit pilot approval from the salon and keep a rapid rollback/forwarding path.

## Intentionally deferred

Outlook/Microsoft 365, CRM integrations, multi-location support, advanced staff optimization, deep
analytics, outbound campaigns, multilingual support, and cross-industry customization are outside
the current salon-first MVP.

Detailed implementation notes from earlier increments are retained in [`docs/history`](history/).
