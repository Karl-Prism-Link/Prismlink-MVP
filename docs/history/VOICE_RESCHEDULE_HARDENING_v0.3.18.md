# PRISM LINK Voice Reschedule Hardening v0.3.18

This increment fixes issues exposed by live browser-voice reschedule testing.

## Changes

1. `find_appointments` now returns canonical service labels plus authoritative `spoken_start` values. If the caller identifies an appointment by a weekday/date that does not match any found appointment, the tool returns `APPOINTMENT_REFERENCE_MISMATCH` and instructs the receptionist to read back the actual appointment rather than inventing a match.
2. Once one appointment is selected, the existing `check_availability` voice handler becomes reschedule-aware. It ignores accidental LLM service IDs/labels and checks the requested move using the appointment's existing service, duration and staff.
3. Reschedule availability prepares a fingerprint for the exact appointment/new start. `reschedule_appointment(confirmed=true)` is rejected unless that exact move was prepared and the latest caller turn is an explicit affirmation.
4. Successful reschedule results tell the assistant to say `rescheduled` or `moved`, not `booked`.

No database migration or environment-variable change is required.
