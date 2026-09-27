# PRISM LINK Routing + Message Fast Path v0.4.6

This increment fixes the observed call pattern where clear routing and message requests such as `Can I speak to Alex?`, `Can I speak to Jo?`, and `Can you leave a message?` escaped to Groq and then degraded into repeated provider-error prompts.

## Runtime behaviour

- `speak/talk to <name>` routing requests are parsed before the LLM.
- The requested person is matched only against configured active salon staff. Unknown names are not invented as staff.
- Direct transfer is not yet wired in the current browser/WebRTC development transport, so the safe routing outcome is: explain that the call cannot be connected directly and offer to take a message.
- A caller accepting that offer enters the deterministic message state machine.
- Direct message requests also enter the deterministic message state machine immediately.
- Message content is captured as caller text, then the callback number is collected with bounded fragmented-digit capture.
- The exact recipient (when configured), message and callback number are prepared and read back.
- Message persistence requires a fresh explicit `Yes` to the prepared details.
- Successful submissions populate the existing tenant-scoped Messages inbox and produce outcome `message_taken`.

## Safety / correctness

- No transfer success is claimed without a telephony transfer result.
- No message is persisted merely because the caller asked to leave one.
- A model-generated `confirmed=true` cannot submit a message that was not first prepared in trusted runtime state.
- Prepared booking/reschedule/cancel state is cleared when the caller deliberately switches into routing/message flow, preventing stale appointment confirmations from colliding with message confirmation.
- Provider-rate-limit recovery for routing/message turns is state-aware and never requires an indefinite repeat loop.

## Expected browser test

Caller: `Can I please speak to Jo?`

PRISM LINK: `I can't connect you directly right now, but I can take a message for Jo. Would you like to leave one?`

Caller: `Yes.`

PRISM LINK: `What message would you like me to leave for Jo?`

Caller: `Please ask Jo to call me about my appointment.`

PRISM LINK: `What number should they call you back on?`

Caller: supplies callback digits.

PRISM LINK: reads back the exact message, recipient and callback number and asks `Is that correct?`

Caller: `Yes.`

PRISM LINK: `I've left your message for Jo.`

The message should then appear in the dashboard Messages inbox.
