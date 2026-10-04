# PRISM LINK voice runtime setup

PRISM LINK uses one tenant-scoped voice runtime for browser/WebRTC testing and inbound telephone
calls through Aurora.

```text
Browser microphone or Aurora SIP/RTP
  -> Pipecat voice pipeline
  -> Deepgram speech services
  -> Groq or OpenRouter
  -> tenant-scoped PRISM LINK tools
  -> booking and Google Calendar
```

## What is implemented

- Browser/WebRTC voice with Pipecat, Deepgram, and the selected LLM provider.
- Tenant-scoped booking, rescheduling, cancellation, enquiries, and messages.
- Explicit confirmation and calendar-conflict checks inside consequential booking tools.
- Aurora SIP event handling and PCMA/PCMU RTP helpers with an isolated pipeline per call.

Telephone integration still needs end-to-end validation. In particular, confirm that Aurora's event
payload uses the UUID format expected by the API, then verify two-way audio, Google Calendar writes,
and call cleanup before using customer calls.

## 1. Install voice dependencies

From the PRISM LINK project directory:

```powershell
pip install -e ".[dev,voice]"
```

The `voice` extra installs Pipecat with Deepgram, Groq, OpenRouter, runner, Silero VAD, WebRTC,
and WebSocket support.

## 2. Configure private environment values

Add provider settings to the ignored `.env` file:

```env
DEEPGRAM_API_KEY=YOUR_DEEPGRAM_KEY
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=YOUR_OPENROUTER_KEY
OPENROUTER_MODEL=openrouter/free
VOICE_TENANT_SLUG=your-existing-salon-slug
VOICE_TEST_CALLER_NUMBER=0213333333
DEEPGRAM_STT_MODEL=nova-3-general
DEEPGRAM_STT_LANGUAGE=en-NZ
DEEPGRAM_TTS_VOICE=aura-2-helena-en
```

Alternatively, set `LLM_PROVIDER=groq` and provide `GROQ_API_KEY`. Keep all provider keys and
shared Aurora control tokens out of source control, chat, logs, and screenshots.

`VOICE_TENANT_SLUG` must match a salon already stored in the database. Browser testing uses this
setting; inbound Aurora calls resolve the tenant by called number, with the configured slug as an
explicit development fallback.

## 3. Start the API and dashboard

```powershell
python -m uvicorn apps.api.main:app --reload
```

Open the dashboard and check **Salon setup → Live voice development**. It reports the current
tenant and whether Deepgram and the selected LLM provider are configured without returning keys.

## 4. Test browser voice

```powershell
python -m apps.voice.bot --transport webrtc
```

Open the local URL printed by Pipecat and allow microphone access. Try an enquiry, then a booking.
The assistant must check availability, collect missing details, and ask for explicit confirmation.
Verify that no booking is created before confirmation and that a confirmed appointment appears in
both the dashboard and Google Calendar.

## 5. Configure and validate inbound calls

Follow [Aurora SIP and Pipecat integration](AURORA_SETUP.md) for the local callback, control API,
shared token, RTP host, and supported codecs. Keep Aurora's callback/control APIs on loopback.
Aurora currently supports mono 8 kHz PCMA/PCMU for this bridge.

Before pilot use, verify event UUID validation, called-number tenant routing, audible two-way audio,
confirmed booking in Google Calendar, caller metadata, hang-up handling, and socket/pipeline cleanup.
Test provider errors and concurrent calls as well. The application code and unit tests cannot prove
carrier routing, host firewall behavior, live media quality, or calendar delivery.

## 6. Booking safeguards

The LLM cannot write directly to the database or Google Calendar. It calls tenant-scoped functions
in `services/voice/tools.py`; consequential operations pass through `BookingService`, which
requires explicit confirmation and checks availability and business rules.
