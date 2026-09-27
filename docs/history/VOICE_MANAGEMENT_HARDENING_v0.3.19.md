# PRISM LINK Voice v0.3.19 — appointment-management hardening

Changes:

- Remembers an appointment candidate that the caller explicitly rejects immediately after lookup.
- Excludes rejected candidates from subsequent appointment selection unless the caller later re-identifies one by its actual returned day/date.
- Returns explicit `APPOINTMENT_REFERENCE_MISMATCH` / `ONLY_REJECTED_APPOINTMENTS_REMAIN` guidance so the bot does not repeatedly ask about the same rejected appointment.
- Successful reschedules now bypass a second LLM completion. The tool result is stored with `run_llm=False` and PRISM LINK speaks a deterministic confirmation from the authoritative tool-returned service and `spoken_start`.
- Cancellation now uses the same prepare/readback/fresh-yes guard as booking and rescheduling, and successful cancellation speech also bypasses a redundant LLM round-trip.
- Booking behavior is unchanged.
