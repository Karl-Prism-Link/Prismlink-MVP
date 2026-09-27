# PRISM LINK Voice State-Machine Hardening v0.4.4

This increment reduces unnecessary Groq dependency in the core booking path and fixes a live Pipecat event/frame ordering race observed during caller name correction.

## Changes

- The deterministic gate now chooses the newest caller utterance across the call-session event stream and `LLMContextFrame`, preventing a stale prior turn from overwriting a newer caller correction.
- Once a booking service is resolved, a caller-supplied day/time is checked directly through the authoritative availability tool without another LLM request.
- Known salon STT variants `mean tea cup` and `means he can't` can resolve to configured `Men's Cut`; exact configured custom services still win first.
- Generated speech is trimmed to at most one question even when a provider emits several questions inside one aggregated text frame.
- Common social turns after a confirmed booking/reschedule/cancellation (`thank you`, `cheers`, `bye`, etc.) are answered deterministically without a Groq request.
- Consequential action confirmation, Google Calendar checks/writes, idempotency and tenant scoping are unchanged.
