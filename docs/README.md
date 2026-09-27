# PRISM LINK documentation

## Current guides

- [Progress report — 20 September 2026](PROGRESS_REPORT_2026-09-20.md) — verified end-to-end
  progress, current risks, and recommended next milestones.
- [Monitoring metrics and alerting](MONITORING_METRICS.md) — production measurement plan for call
  outcomes, voice quality, AI latency, booking integrity, reliability, privacy, and cost.
- [MVP and pilot status](MVP_STATUS.md) — current capability and readiness assessment.
- [Voice and FreeSWITCH setup](VOICE_SETUP.md) — browser voice, transport configuration, and
  real-call validation.
- [Google Calendar OAuth setup](GOOGLE_OAUTH_SETUP.md) — tenant-specific local OAuth setup.
- [OpenRouter setup](OPENROUTER_SETUP.md) — optional development LLM provider.

## Historical implementation notes

[`history/`](history/) contains short notes for completed voice and dashboard increments. They are
retained for engineering context, but the current guides above are authoritative when the files
disagree.

Secrets and provider credentials must stay in the ignored `.env` or managed secret storage. Never
place real credentials in documentation.
