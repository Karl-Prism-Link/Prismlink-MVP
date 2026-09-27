# PRISM LINK After-Hours Booking + Message Correction Hardening v0.4.9

## Purpose
Close the gaps exposed by the after-hours test where a clear booking request fell through to provider fallback and callback wording confused the caller name with a staff recipient.

## Behaviour
- `Can I book an appointment?` is handled deterministically and asks which configured service the caller wants.
- The fact that the call occurs after salon closing time does not itself force a message fallback; appointment availability is still governed by configured business hours for the requested appointment time.
- Callback wording such as `get them to ring Georgie at 021...` starts the message flow, stores Georgie as the callback contact, keeps the message recipient as the salon unless an explicit staff recipient was requested, and captures the caller-spoken digits directly.
- `I'm Georgie` while a message is awaiting confirmation updates the callback name and invalidates the old proposal.
- `The message should just say ring Georgie` replaces the message text and requires a fresh readback and confirmation.
- If a caller corrects an already submitted message during the same call, the existing call-linked message is updated only after a fresh confirmation; a duplicate message row is not created.

## Trust boundary
The LLM does not authorize message submission. The runtime stores the exact prepared message/name/phone/recipient payload. Any material correction invalidates that prepared payload and requires another explicit confirmation before persistence.
