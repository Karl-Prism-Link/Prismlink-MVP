# PRISM LINK v0.3.1 — OpenRouter test LLM

OpenRouter is an optional **development/testing** LLM provider for the browser voice runtime. Groq remains the default provider in the current Salon-First architecture.

## Configure

Install/update voice dependencies:

```powershell
pip install -e ".[dev,voice]"
```

Add to `.env`:

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=YOUR_OPENROUTER_API_KEY
OPENROUTER_MODEL=openrouter/free
```

Keep `DEEPGRAM_API_KEY`, `VOICE_TENANT_SLUG`, Google OAuth settings, database settings, and the rest of the existing `.env` unchanged. `GROQ_API_KEY` may remain blank while `LLM_PROVIDER=openrouter`.

Do not paste API keys into chat, source control, logs, or screenshots.

## Run

Terminal 1:

```powershell
python -m uvicorn apps.api.main:app --reload
```

Terminal 2:

```powershell
python -m apps.voice.bot --transport webrtc
```

The dashboard Live voice card should report `LLM openrouter configured`. The voice terminal logs the selected provider and model but never logs the API key.

## Free-model router

`OPENROUTER_MODEL=openrouter/free` lets OpenRouter select from its currently available free models. This is useful for smoke testing and budget-sensitive development, but the selected model can vary, so response quality and latency are not deterministic.

For repeatable tests, choose a specific OpenRouter model slug that currently supports tool/function calling, and set that slug as `OPENROUTER_MODEL`. Free model availability changes over time.

## Switch back to Groq

```env
LLM_PROVIDER=groq
GROQ_API_KEY=YOUR_GROQ_API_KEY
GROQ_MODEL=llama-3.3-70b-versatile
```

Restart the API and voice process after changing providers.
