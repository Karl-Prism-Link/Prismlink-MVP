# PRISM LINK Booking Intent Normalization v0.4.10

## Live regression fixed

Deepgram/caller wording `I book in an appointment?` could reach the LLM before the user-turn state callback marked the call as booking intent. Because the older deterministic regex did not recognise `book in an appointment`, a Groq failure then produced the generic connection fallback.

## Change

- Added one shared deterministic `is_generic_booking_request()` classifier.
- Covers clear booking wording plus common STT variants including `book in an appointment`, `book me in`, and `make a booking`.
- Used both in the pre-LLM booking gate and the user-turn state callback so Pipecat event/frame ordering cannot create this race.
- Explicit cancel/reschedule/move/change wording is excluded so appointment-management intents retain priority.
- This classifier only establishes intent; it does not authorise any booking write.

## Expected live behaviour

Caller: `I book in an appointment?`

PRISM LINK: `Sure, what service would you like to book?`
