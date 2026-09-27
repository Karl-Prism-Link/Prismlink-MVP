# PRISM LINK Voice Management Fallback v0.3.21

This patch stops cancellation/reschedule conversations from looping when the caller cannot remember the booking phone number.

- Booking-phone verification remains mandatory before appointment details can be disclosed or changed.
- A clear "I can't remember it" marks the booking phone as unavailable for the current management flow.
- The assistant must not keep asking for the same booking phone. It offers a staff follow-up message and asks for a callback number instead.
- A callback number is for follow-up only and is never treated as appointment ownership verification.
- A callback number supplied after fallback is never promoted to booking identity.
- If the caller later explicitly says they remembered/found the real booking phone, normal verified management can resume.
