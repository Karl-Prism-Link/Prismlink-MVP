# PRISM LINK Salon Hours Fast Path v0.4.8

## Purpose
Keep configured opening-hours enquiries out of the LLM/provider path so callers get a fast, truthful answer even when Groq is rate-limited or unavailable.

## Behaviour
- `Are you open now?` is answered from the tenant timezone and saved business hours.
- Before opening: states today's opening time.
- During opening hours: states that the salon is open and today's closing time.
- After closing: states that the salon is closed and gives the next configured opening period.
- General opening-hours questions are answered from saved business hours.
- The call outcome is recorded as `enquiry_resolved`.
- No LLM call is required for these questions.

## Scope
This patch does not introduce or decide a configurable after-hours booking policy. It fixes the common-enquiry path for opening-hours questions. After-hours booking/message policy remains a separate call-flow hardening item.
