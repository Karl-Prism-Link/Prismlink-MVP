# PRISM LINK Service-First Booking Fast Path v0.4.5

This increment fixes a live failure where a caller opened with only a configured service name (for example, `Means Cut.`), then supplied `Tomorrow.` or a weekday. The service-only first turn had not established booking context, so later date-only turns could escape to Groq and hit the provider fallback.

## Changes

- A configured service reply itself is now sufficient to enter the deterministic booking state machine, even when the caller has not literally said `book` or `appointment`.
- Relative/day-only caller turns such as `Tomorrow` and `Wednesday` are handled deterministically once the service is known: PRISM LINK stores the caller day phrase and asks only for the missing time.
- Groq rate-limit fallback is now state-aware. If deterministic booking state already identifies the next missing field, PRISM LINK asks for that field instead of repeatedly saying `Could you repeat that?`.
- Booking confirmation, availability, business-hours logic, phone/name capture, idempotency, and Google Calendar writes remain deterministic and unchanged.
