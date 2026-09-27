# PRISM LINK Voice Management Identity — v0.3.20

## Purpose
Prevent reschedule/cancel flows from disclosing or mutating another customer's appointment based only on a guessed date/time, customer name, or development caller ID.

## Rules
- The booking phone number must appear in a finalized caller transcript before appointment details are disclosed or changed.
- Caller ID is a lookup hint only and is not treated as verification.
- A caller-supplied name constraint persists across follow-up lookup turns.
- Reschedule and cancellation tools verify that the selected appointment belongs to the caller-spoken booking phone.
- Cancellation prepares the exact appointment before asking the final question, so one fresh explicit yes is sufficient.
- Success speech remains deterministic after the mutation tool returns ok=true.
