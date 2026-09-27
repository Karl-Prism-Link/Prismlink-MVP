# PRISM LINK v0.3.22 — salon message inbox

This increment turns voice fallback messages into a first-class salon follow-up queue.

- `salon_messages` stores one follow-up message per call with caller name, callback number, message text, status, timestamps and call linkage.
- Live `take_message` persists the inbox record immediately while retaining the existing `message_taken` call summary/outcome.
- The simulator also records `message_taken` outcomes in the inbox so the workflow can be tested without telephony.
- `/api/v1/messages` lists tenant-scoped messages.
- `PATCH /api/v1/messages/{id}` supports `new`, `contacted`, and `resolved` states.
- `/api/v1/messages/{id}/transcript` exposes only the associated tenant-scoped stored transcript.
- The dashboard has a Messages tab, a New messages KPI, status controls, and an associated transcript viewer.

Existing call logs remain unchanged. New messages created after installing v0.3.22 appear in the inbox; earlier `message_taken` call summaries remain visible under Calls.
