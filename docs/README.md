# PRISM LINK documentation

## Current guides

- [Monitoring metrics and alerting](MONITORING_METRICS.md) — operational measurements for Aurora,
  Pipecat, booking integrity, reliability, privacy, and cost.
- [MVP and pilot status](MVP_STATUS.md) — current capability and readiness assessment.
- [Voice runtime setup](VOICE_SETUP.md) — browser voice configuration and validation.
- [Aurora integration setup](AURORA_SETUP.md) — SIP/RTP integration, environment, and live-call checks.
- [Google Calendar OAuth setup](GOOGLE_OAUTH_SETUP.md) — tenant-specific local OAuth setup.
- [OpenRouter setup](OPENROUTER_SETUP.md) — optional development LLM provider.

## Historical implementation notes

[`history/`](history/) contains short notes for completed voice and dashboard increments. They are
retained for engineering context, but the current guides above are authoritative when the files
disagree.

Secrets and provider credentials must stay in the ignored `.env` or managed secret storage. Never
place real credentials in documentation.
