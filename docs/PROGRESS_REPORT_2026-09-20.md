# PRISM LINK progress report

**Reporting date:** 20 September 2026
**Current stage:** Working end-to-end MVP; pre-pilot validation and operational hardening

## Executive summary

PRISM LINK can now receive a real telephone call through 2talk and FreeSWITCH, stream the caller's
audio into the Pipecat runtime, transcribe speech with Deepgram, generate a response with Groq, and
play Deepgram-generated speech back to the caller. This is the first verified end-to-end telephone
call through the intended production-style architecture.

The core salon-first product is substantially implemented: salon setup, tenant isolation, Google
Calendar integration, availability, booking, rescheduling, cancellation, calls, transcripts,
summaries, messages, and the dashboard all exist. The repository passes its full automated test and
quality suite.

PRISM LINK is not yet ready for unattended customer traffic. The next phase is controlled real-call
scenario testing, fault handling, monitoring, privacy and retention decisions, credential hardening,
and a small salon pilot with a rapid rollback path.

## Current working architecture

```text
Caller
  -> 2talk public number
  -> 2talk registered SIP trunk
  -> FreeSWITCH on the home microserver
  -> mod_audio_stream bidirectional PCM WebSocket
  -> PRISM LINK Pipecat voice runtime
  -> Deepgram speech-to-text
  -> deterministic salon workflow and Groq
  -> tenant-scoped booking and information tools
  -> Deepgram text-to-speech
  -> FreeSWITCH and the caller
```

The deployed voice listener is bound to loopback on port `8765`. FreeSWITCH and the PRISM LINK
voice service are managed as system services on the Ubuntu microserver.

## Progress completed

### Product and salon workflows

- Salon-first, tenant-aware application structure is implemented.
- Owner registration, authentication, and the 14-day trial boundary are implemented.
- Salon profile, services, staff, business hours, phone number, timezone, and greeting are
  configurable.
- Availability, booking, rescheduling, and cancellation use tenant-scoped services and require
  explicit confirmation before consequential changes.
- Booking concurrency protection, idempotency, and conflict checks are implemented and tested.
- Calls, turn transcripts, summaries, outcomes, messages, and appointment references can be
  persisted for the dashboard.
- Google Calendar OAuth and per-tenant calendar actions are implemented for development and pilot
  use.

### Voice runtime

- Browser/WebRTC voice is implemented with Pipecat.
- Deepgram STT and TTS are configured and produced speech successfully during the live call.
- Groq generated the live response successfully; the captured run showed normal LLM and TTS
  metrics.
- The deterministic voice layer covers common salon enquiries, booking details, confirmations,
  corrections, messages, hours, rescheduling, and cancellation behavior.
- Each FreeSWITCH media connection creates an isolated call pipeline and passes caller and called
  number metadata into PRISM LINK.

### FreeSWITCH and carrier integration

- FreeSWITCH `1.11.4-dev` is active on the home microserver.
- The 2talk registered trunk is registered and reports `UP`.
- The public 2talk number is assigned to the registered-trunk pilot.
- The bidirectional `mod_audio_stream` module is installed and loaded.
- The inbound dialplan accepts both the public number and the 2talk pilot destination.
- The PRISM LINK voice service starts automatically through systemd and listens on
  `127.0.0.1:8765`.
- A real inbound call was answered, streamed into PRISM LINK, processed by Deepgram and Groq, and
  returned audible synthesized speech to the caller.
- Four inbound FreeSWITCH calls were recorded during setup and validation, with no gateway-level
  failed inbound calls.

### Repository and documentation

- Obsolete implementation material was removed and current guides were separated from historical
  release notes.
- Current setup, MVP status, Google OAuth, OpenRouter, and historical voice notes are organized
  under `docs/`.
- A repeatable microserver installer is available at `ops/install_microserver_freeswitch.sh`.
- Generated remote-attachment files are now ignored by Git.
- The current repository is on `main` at commit `e979b4c` before the documentation and operations
  files from this work are committed.

## Verification evidence

| Check | Result |
|---|---|
| Automated tests | **139 passed** |
| Ruff lint | **Passed** |
| Ruff formatting check | **123 files formatted correctly** |
| Git whitespace check | **Passed** |
| FreeSWITCH service | **Active** |
| PRISM LINK voice service | **Active** |
| 2talk SIP gateway | **Registered / UP** |
| FreeSWITCH media WebSocket | **Connected during live call** |
| Deepgram STT/TTS | **Used successfully during live call** |
| Groq LLM | **Used successfully during live call** |
| Telephone audio returned to caller | **Confirmed by user** |

The automated suite currently emits ten dependency deprecation warnings. These do not fail the
suite, but Pipecat runner and worker APIs should be updated before the relevant compatibility
removals.

## Known gaps and risks

### Must resolve before a salon pilot

1. **Complete a real-call scenario pack.** Enquiry, availability, booking, rescheduling,
   cancellation, message capture, after-hours behavior, interruption, silence, and caller hang-up
   must be repeated and recorded as pass/fail results.
2. **Fix post-call cleanup.** One successful live call logged a non-fatal
   `cannot schedule new futures after shutdown` error after disconnect. The call completed, but
   cleanup should be deterministic and error-free.
3. **Rotate the previously exposed Google OAuth client secret.** It was removed from the current
   tree but must be treated as compromised because it remains in Git history until the credential
   is rotated.
4. **Harden secret storage.** Provider and OAuth credentials currently depend on protected local
   files. A managed secret store and documented rotation process are required for production.
5. **Add monitoring and alerts.** Service health, failed calls, provider failures, WebSocket loss,
   latency, and booking reconciliation need structured metrics and actionable alerts.
6. **Define privacy controls.** Recording and transcript consent, access, retention, export, and
   deletion need an explicit New Zealand privacy review and operating policy.
7. **Restrict and document network exposure.** SIP access, event-socket access, firewall rules, and
   permitted 2talk network ranges need a reviewed production baseline.

### Validate during pilot hardening

- Unknown numbers must fail closed and never select the wrong salon.
- Concurrent calls must remain isolated and respect the configured capacity limit.
- Provider timeout, authentication failure, and partial booking failure must produce safe caller
  behavior and a follow-up record.
- Google Calendar booking, rescheduling, and cancellation must be proven through real calls, not
  only browser and automated tests.
- Database backup, service rollback, restart recovery, and carrier-diversion procedures need an
  operator runbook.
- SQLite is suitable for the current controlled deployment but should be reviewed against the
  expected pilot concurrency and recovery requirements.

## Recommended next milestones

### Milestone 1 — repeatable live-call validation

- Create a short scripted test matrix and record every call result.
- Verify persisted call, transcript, summary, message, and appointment records after each call.
- Fix the post-disconnect cleanup error and rerun hang-up tests.
- Measure typical response latency and audio quality over several calls.

### Milestone 2 — safe booking pilot

- Connect and verify the pilot salon's Google Calendar.
- Complete real-call booking, rescheduling, and cancellation tests with explicit confirmation.
- Add monitoring, alerting, backup, and rollback instructions.
- Rotate exposed credentials and document ownership of every production secret.

### Milestone 3 — controlled salon trial

- Obtain explicit approval from one salon.
- Start with limited forwarding hours and a rapid diversion fallback.
- Review transcripts and outcomes daily during the initial trial.
- Expand traffic only after the scenario pack and operational checks remain stable.

## Overall assessment

PRISM LINK has crossed the most important technical threshold: it is now a real telephone AI
receptionist, not just a browser demonstration. The remaining work is primarily reliability,
security, operational readiness, and proving the complete salon workflow under real-call
conditions. The appropriate next step is a controlled validation phase, followed by a tightly
managed single-salon pilot—not broad customer deployment.
