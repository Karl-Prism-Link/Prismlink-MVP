# PRISM LINK Voice Meridiem Inference v0.3.12

Bare spoken clock times such as “2 o’clock” are now resolved conservatively against the salon’s configured business hours.

- If exactly one of 2 AM / 2 PM fits the appointment duration inside business hours, PRISM LINK uses that interpretation.
- If both fit, the receptionist asks AM or PM.
- If neither fits, the receptionist asks for another time.
- Explicit AM/PM always wins and is never rewritten.

Example for a salon open 09:00–17:00: “Monday at 2 o’clock” resolves to Monday 14:00.
