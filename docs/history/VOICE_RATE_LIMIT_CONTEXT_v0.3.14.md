# PRISM LINK v0.3.14 — Groq rate-limit and voice-context optimization

This increment reduces per-turn LLM token pressure and makes live voice latency measurable before SIP/FreeSWITCH work.

## Changes

- Groq completion output is capped at 256 tokens by default.
- GPT-OSS models use `reasoning_effort=low` and exclude returned reasoning from the response.
- Groq's OpenAI-compatible client is configured for one automatic retry and a 10-second request timeout.
- A non-fatal 429 that remains after the bounded provider retry produces a short spoken fallback instead of dead air.
- Pipecat `UserBotLatencyObserver` logs user-to-bot latency and per-service breakdowns.
- LLM history is bounded to the most recent four user turns by default.
- A compact structured voice state preserves booking intent, service, date/time phrase, customer details, appointment ID, confirmation state, and last tool result across history trimming.
- Tool schemas were shortened to reduce prompt tokens and no longer require the LLM to invent an ISO date when the caller's natural-language time phrase can be resolved by PRISM LINK.
- Reschedule now uses the same deterministic caller-date/time resolution path as booking.

## Optional `.env` overrides

```env
GROQ_MAX_COMPLETION_TOKENS=256
GROQ_MAX_RETRIES=1
GROQ_TIMEOUT_SECONDS=10
GROQ_REASONING_EFFORT=low
VOICE_CONTEXT_TURNS=4
```

For the current Groq GPT-OSS test:

```env
LLM_PROVIDER=groq
GROQ_MODEL=openai/gpt-oss-20b
```

No database migration or new package dependency is required.
