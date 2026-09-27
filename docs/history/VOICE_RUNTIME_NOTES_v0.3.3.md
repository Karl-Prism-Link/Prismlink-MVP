# PRISM LINK v0.3.3 voice test notes

This increment adds a Business Hours editor to Salon setup and exposes the configured LLM model name in the safe voice-status card.

## Why
The live voice test successfully completed browser/WebRTC -> Deepgram STT -> OpenRouter -> Deepgram TTS, but a caller asking about opening hours received a fallback because no business hours were configured through the dashboard. The backend hours API already existed; this update exposes it in the UI.

## Test after applying
1. Sign in at http://localhost:8000.
2. Open Salon setup -> Business hours.
3. Configure all seven days and save.
4. Restart the voice bot if it is already running.
5. Ask: "What time are you open on Thursday?"
6. Then ask a configured service question and try a booking.

The voice-status card now also displays the selected LLM model, making it clear when `openrouter/free` is active.
