# PRISM LINK Voice Service Clarification Fast Path v0.4.3

This patch removes an unnecessary Groq dependency after a service ambiguity.

When a caller gives a generic service plus a valid date/time (for example, "a cut on Thursday at 3") and then clarifies with a configured alias such as "means cut", the deterministic gate now:

1. resolves the configured service,
2. recovers the caller's already-spoken date/time from recent finalized transcripts,
3. checks availability directly with the existing deterministic booking tool, and
4. continues to name capture without another LLM request when the slot is available.

This also removes a Pipecat event-order race: the gate independently resolves the service reply instead of requiring the user-turn event to have updated structured state first.

No appointment write is performed here. Existing explicit-confirmation and idempotency gates remain unchanged.
