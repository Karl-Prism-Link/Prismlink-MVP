# PRISM LINK — Voice and FreeSWITCH setup

PRISM LINK uses one tenant-scoped voice runtime through either browser/WebRTC or a FreeSWITCH media
WebSocket:

**microphone → Pipecat transport → Deepgram STT → deterministic state/turn router → selected LLM tier (Groq) or OpenRouter → PRISM LINK tools → Deepgram TTS → speaker**

It persists the live call, turn transcripts, outcome, and summary into the same tenant-scoped database used by the dashboard.

## What this stage proves

- Real streaming speech-to-text and text-to-speech.
- A selectable Groq or OpenRouter LLM constrained to salon booking, reschedule, cancellation, enquiries, and message tools.
- The same `BookingService` and Google Calendar provider used by the simulator.
- Explicit confirmation enforced again inside the consequential booking tools.
- Live call transcript/outcome/summary persistence.
- Browser microphone testing for the same core receptionist behavior used by telephone calls.
- FreeSWITCH media WebSocket input/output with one isolated runtime per call.
- Called-number tenant routing with an explicit development fallback.

The FreeSWITCH code path is implemented, automated-test covered, and connected to the deployed
2talk and FreeSWITCH services. An initial live call verified inbound SIP, RTP, bidirectional PCM,
Deepgram STT/TTS, Groq, and audible telephone playback. The current phase is repeatable scenario,
failure-path, concurrency, cleanup, and operational validation before customer calls.

## 1. Install the voice dependencies

From the PRISM LINK project directory with the virtual environment activated:

```powershell
pip install -e ".[dev,voice]"
```

The `voice` extra installs Pipecat with Deepgram, Groq, OpenRouter, runner, Silero VAD, WebRTC, and WebSocket support.

## 2. Add provider settings to `.env`

Keep your existing Google OAuth settings. Add:

```env
DEEPGRAM_API_KEY=YOUR_DEEPGRAM_KEY

# Use OpenRouter for low/no-cost development testing:
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=YOUR_OPENROUTER_KEY
OPENROUTER_MODEL=openrouter/free

# Or use the architecture-default Groq routed path:
# LLM_PROVIDER=groq
# GROQ_API_KEY=YOUR_GROQ_KEY
# GROQ_FAST_MODEL=openai/gpt-oss-20b
# GROQ_REASONING_MODEL=openai/gpt-oss-120b
# GROQ_REASONING_EFFORT=low

VOICE_TENANT_SLUG=your-existing-salon-slug
VOICE_TEST_CALLER_NUMBER=0213333333

DEEPGRAM_STT_MODEL=nova-3-general
DEEPGRAM_STT_LANGUAGE=en-NZ
DEEPGRAM_TTS_VOICE=aura-2-helena-en
```

Do not paste provider keys into chat, source control, logs, or screenshots.

### OpenRouter testing notes

`OPENROUTER_MODEL=openrouter/free` uses OpenRouter's free-model router. It is convenient for development and OpenRouter filters for request capabilities such as tool calling when tools are present. Because the free router can select different available models, response quality and latency can vary between calls. For repeatable voice tests, set `OPENROUTER_MODEL` to a specific OpenRouter model slug that currently supports tool calling (often a `:free` variant when available).

Groq remains the intended architecture-default LLM provider in the current Salon-First PRD. OpenRouter support in this package is a development/testing option and does not change the canonical MVP architecture unless the PRD is updated.

In v0.4, Groq no longer has to use one model for every turn. `GROQ_FAST_MODEL` handles low-reasoning conversational turns and `GROQ_REASONING_MODEL` handles booking date/time, appointment management, corrections, and recovery. Prepared booking/reschedule/cancel confirmations bypass the LLM entirely. `GROQ_MODEL` is a legacy single-model setting and is not the v0.4 voice router selector.

`VOICE_TENANT_SLUG` must exactly match the slug of a salon already stored in the database. It selects the browser tenant and is also the FreeSWITCH fallback if a call does not supply a called number. A FreeSWITCH URL may include `called_number=<DID>` to resolve the tenant from the configured salon phone number.

`VOICE_TEST_CALLER_NUMBER` acts as the browser caller ID. Set it to a number used by an existing test appointment when testing reschedule/cancellation lookup.

## 3. Start the normal API/dashboard

Terminal 1:

```powershell
python -m uvicorn apps.api.main:app --reload
```

Open the dashboard, sign in, and check **Salon setup → Live voice development**. It should report the current tenant, whether the configured voice tenant matches, and whether Deepgram and the selected LLM provider are configured. It never returns the actual API keys.

## 4. Start the voice process

Open Terminal 2 in the same project directory and activate the same virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
python -m apps.voice.bot --transport webrtc
```

Use the local browser URL printed by the Pipecat runner and allow microphone access.

The voice runtime is intentionally a separate process from the dashboard/API. Both processes use the same database and salon configuration.

## 5. First voice tests

### Test A — enquiry

Say:

> What services do you offer?

Expected behavior:

- speech is transcribed;
- The selected LLM calls the salon-information tool rather than inventing services;
- the assistant answers with configured salon data;
- the call appears in the dashboard after disconnect.

### Test B — booking

Say something like:

> I'd like to book a haircut on Thursday at 2 PM.

Expected behavior:

1. The assistant resolves the configured service.
2. It checks availability through the existing calendar provider.
3. It asks for any missing customer details.
4. It repeats the final service/time/name/staff details and explicitly asks for confirmation.
5. Do **not** confirm immediately on the first run. Verify no appointment is created.
6. Continue the conversation and clearly say yes/confirm.
7. Only then should the booking tool create the appointment.
8. Verify the event appears in Google Calendar and in the PRISM LINK dashboard.

### Test C — reschedule/cancel

Set `VOICE_TEST_CALLER_NUMBER` to the same phone number used by the test appointment. Restart the voice process, then say:

> I need to move my appointment.

The assistant should use tenant-scoped appointment lookup, identify the appointment safely, check the replacement time, ask for explicit confirmation, and update the existing event rather than create a duplicate.

Then test cancellation and verify the assistant obtains explicit confirmation before deleting the Google event and marking the local appointment cancelled.

## 6. Call persistence

For each completed browser voice session, PRISM LINK now writes:

- `calls` row with live call status/outcome;
- turn-level `transcript_messages` for caller and assistant;
- `call_summaries` with the final outcome and follow-up state;
- appointment references when a booking tool succeeds.

A disconnected call that never reaches a safe resolution is stored as `failed_fallback` with follow-up required rather than being silently treated as successful.

## 7. Safety boundaries in the code

The LLM is not allowed to write directly to the database or Google Calendar. It can only call tenant-scoped functions in `services/voice/tools.py`. Consequential functions still invoke the existing `BookingService`, which independently rejects unconfirmed booking/reschedule/cancellation attempts.

The current browser voice toolset also rejects past times and times outside configured salon business hours before a booking write.

## 8. Troubleshooting

### `VOICE_TENANT_SLUG ... does not match a salon`

Use the exact slug from the salon account you created. The dashboard's Live voice development card shows both the current tenant slug and the configured slug.

### `DEEPGRAM_API_KEY is required` / selected LLM API key is required

Add the missing key to `.env`, save the file, and restart the voice process.

### Browser opens but there is no audio

Confirm microphone permission is allowed, the voice process is still running, and the terminal does not show a provider authentication error.

### Booking says calendar unavailable

Confirm the salon is still connected to Google Calendar in the dashboard and that the tenant-specific OAuth credential store is available.

### Booking is refused as outside business hours

Either choose a time inside the configured salon hours or configure the hours in Salon setup.

## 9. FreeSWITCH listener

Set the listener values in the same private `.env` file:

```env
FS_WS_HOST=127.0.0.1
FS_WS_PORT=8765
FS_SAMPLE_RATE=16000
FS_MAX_CALLS=10
FS_RECEIVE_TIMEOUT=60
FS_SEND_TIMEOUT=2
# Required when the listener is reachable beyond loopback.
FS_WS_TOKEN=
```

Start it in a separate terminal:

```powershell
python -m apps.voice.bot_freeswitch
```

The listener accepts `/calls/<FreeSWITCH-UUID>`. It creates a new PRISM LINK database session, toolset, LLM context, STT/TTS services, pipeline worker, transcript, and call record for every connection. The optional URL query values `called_number`, `caller_number`, and `tenant_slug` are read once when the connection opens. Prefer `called_number` for tenant routing; `tenant_slug` is a development fallback.

The transport assumes a bidirectional `mod_audio_stream` build that accepts raw signed 16-bit mono PCM for playback. Keep the listener on loopback or protect it with a token and private TLS-enabled network path. Do not put provider keys or the FreeSWITCH token in source control.

## 10. Live-call validation checklist

Work through these checks against the deployed FreeSWITCH instance and a test DID:

1. Confirm the media WebSocket opens at `/calls/<FreeSWITCH-UUID>` and remains connected.
2. Confirm the configured 8/16 kHz signed 16-bit mono PCM matches the FreeSWITCH module in both
   directions.
3. Confirm caller speech reaches Deepgram and returned TTS is audible on the telephone.
4. Confirm `called_number` selects the intended salon and an unknown DID fails closed.
5. Confirm `caller_number` and the FreeSWITCH UUID appear on the persisted call.
6. Exercise enquiry, booking, rescheduling, cancellation, message, and after-hours scenarios.
7. Test barge-in, silence, hang-up during speech, WebSocket loss, and each provider-failure path.
8. Run concurrent calls and confirm audio, transcripts, tenants, and appointment writes never mix.

Record the result of each deployed-system check in the integration work log. Automated tests prove
the application boundary, but they cannot prove the carrier, FreeSWITCH build, codec, firewall, or
host configuration.
