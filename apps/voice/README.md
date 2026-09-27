# Voice runtime

`bot.py` is the live voice runtime. It uses Pipecat WebRTC for browser microphone testing, and the same pipeline can now be entered through `FreeSwitchAudioStreamTransport` for FreeSWITCH media WebSockets. Both routes use Deepgram for STT/TTS, Groq or OpenRouter for bounded function calling, and `services/voice/tools.py` for all salon actions.

The voice LLM never writes appointments directly. Booking, reschedule, and cancellation pass through the existing tenant-scoped `BookingService`, including explicit-confirmation and calendar-conflict checks.

`runtime.py` remains the deterministic fallback classifier used by the text simulator.

Run the live browser pipeline with:

```powershell
pip install -e ".[dev,voice]"
python -m apps.voice.bot --transport webrtc
```

Required `.env` values are `DEEPGRAM_API_KEY`, `VOICE_TENANT_SLUG`, and the key for the selected `LLM_PROVIDER` (`GROQ_API_KEY` or `OPENROUTER_API_KEY`).

Start the FreeSWITCH listener with:

```powershell
python -m apps.voice.bot_freeswitch
```

Each WebSocket UUID creates a fresh pipeline and call record. Add `called_number`, `caller_number`,
or `tenant_slug` as URL query values when your FreeSWITCH controller can supply them. `called_number`
is resolved against the salon's configured phone number; otherwise the listener uses
`VOICE_TENANT_SLUG` as its development fallback. See
[`docs/VOICE_SETUP.md`](../../docs/VOICE_SETUP.md) for configuration and the live-call validation
boundary.
