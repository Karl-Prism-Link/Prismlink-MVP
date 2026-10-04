# Voice runtime

`bot.py` provides the Pipecat voice pipeline for browser/WebRTC sessions. The API starts an
isolated pipeline for each inbound Aurora SIP call using Aurora's RTP bridge. Both paths use
Deepgram for speech services, Groq or OpenRouter for bounded function calling, and
`services/voice/tools.py` for salon actions.

The voice LLM never writes appointments directly. Booking, rescheduling, and cancellation pass
through the tenant-scoped `BookingService`, including explicit-confirmation and calendar-conflict
checks. `runtime.py` remains the deterministic fallback classifier used by the text simulator.

Run browser voice:

```powershell
pip install -e ".[dev,voice]"
python -m apps.voice.bot --transport webrtc
```

Required private `.env` values are `DEEPGRAM_API_KEY`, `VOICE_TENANT_SLUG`, and the selected
LLM provider key (`GROQ_API_KEY` or `OPENROUTER_API_KEY`). For inbound SIP setup, RTP requirements,
and live-call checks, follow [Aurora integration setup](../../docs/AURORA_SETUP.md) and
[voice setup](../../docs/VOICE_SETUP.md).
