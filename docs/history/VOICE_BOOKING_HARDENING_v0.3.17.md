# PRISM LINK voice booking hardening — v0.3.17

This increment hardens the successful salon booking flow in three areas.

## Canonical service labels

Common service variants are mapped conservatively to stable labels such as `Men's Cut`, `Women's Cut`, `Blow Wave`, and `Colour`. Unknown/custom salon service names are preserved. New services are canonicalized on creation, and the dashboard has an explicit **Clean up service names** action for existing common variants.

## Deterministic phone capture and confirmation

Phone digits are extracted from finalized caller transcripts. The booking tool no longer trusts the LLM to reconstruct the phone number. It returns `spoken_phone` as individual digit words and uses a two-phase flow:

1. `book_appointment confirmed=false` validates the service/time/name, recovers the phone from caller speech, stores a prepared booking fingerprint, and returns exact confirmation details.
2. The receptionist reads back the returned details and asks for explicit confirmation.
3. `book_appointment confirmed=true` succeeds only when the details match the prepared fingerprint and the caller's latest finalized turn is an explicit affirmation.

If no plausible phone digit run is present in caller speech, the tool asks for the number again instead of repairing or inventing digits.

## Duplicate speech suppression

The live Pipecat pipeline now aggregates LLM text into sentence-level frames before TTS. An exact duplicate sentence within the same caller turn is dropped, including duplicates that occur across tool-call continuation cycles. The deduper resets when the next caller turn completes.
