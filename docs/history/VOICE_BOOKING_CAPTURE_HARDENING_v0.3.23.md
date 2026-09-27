# PRISM LINK voice booking capture hardening v0.3.23

- Locks the exact prepared booking details across the final confirmation.
- Requires at least nine caller-spoken phone digits for booking.
- Supports bounded phone digit fragments while the receptionist is explicitly collecting a phone number.
- Treats explicitly spelled caller names as authoritative.
- Retains canonical service state for common STT variants such as `means` -> `Men's Cut`.
- Tightens booking question order and filler-prefixed duplicate speech suppression.
