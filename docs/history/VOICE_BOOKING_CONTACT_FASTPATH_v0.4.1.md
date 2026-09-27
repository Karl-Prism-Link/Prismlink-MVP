# PRISM LINK Voice Booking Contact Fast Path v0.4.1

This patch removes the LLM from the name/phone collection portion of a booking once service, time, and availability are authoritative.

## Changes
- Accept trailing caller spelling such as `Cow. K a r l.` as authoritative `Karl`.
- Arm an explicit booking-name capture state when the receptionist asks for the caller's name.
- Capture simple names deterministically only while that state is active.
- Capture phone digits deterministically, including multi-turn fragments.
- Prepare the booking directly from structured state and speak the exact confirmation readback without another LLM completion.
- Keep explicit caller confirmation and deterministic commit unchanged.

This prevents Groq rate limits from trapping the caller in repeated provider-fallback prompts during contact collection.
