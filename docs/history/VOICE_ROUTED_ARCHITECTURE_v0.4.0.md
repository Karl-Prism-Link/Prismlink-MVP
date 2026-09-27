# PRISM LINK voice routed architecture — v0.4.0

v0.4.0 separates conversational interpretation from correctness-critical salon state.

## Runtime path

`Deepgram STT -> deterministic transcript/state normalizers -> deterministic turn router -> Groq -> PRISM LINK tools -> Deepgram TTS`

The router selects a **fast** or **reasoning** model per completion and can remove tool schemas on turns that are only phrasing a completed tool result. The model never owns booking truth.

## Deterministic no-LLM confirmations

When a booking, reschedule, or cancellation has already been prepared and read back, a short fresh caller `yes` or `no` is handled before the LLM:

- `yes` -> commit the exact prepared mutation -> deterministic success speech
- `no` -> clear the prepared mutation -> deterministic next question
- mixed corrections such as `no, make it four` are not swallowed; they continue to the LLM for interpretation

This removes an LLM round-trip from the most consequential confirmation turn while preserving the existing tool-side explicit-confirmation checks.

## Routing policy

**Fast tier**

- greetings, thanks, short social replies
- configured salon enquiries
- already-normalized short service replies
- name/phone collection after service and time are authoritative
- successful tool-result readback (with tools disabled for that continuation)

**Reasoning tier**

- booking date/time turns
- reschedule/cancel/appointment-management turns
- corrections and recovery
- multi-service requests
- prior tool failures or ambiguity

**Deterministic tools remain authoritative for**

- service canonicalization hints and caller-spelled names / phone digits
- date/time resolution and business-hours inference
- availability and calendar conflicts
- appointment ownership
- final booking/reschedule/cancel confirmation and writes

## Groq configuration

The router uses:

- `GROQ_FAST_MODEL`
- `GROQ_REASONING_MODEL`
- `GROQ_REASONING_EFFORT` for reasoning-tier GPT-OSS requests

Fast-tier GPT-OSS requests always use low reasoning. `GROQ_MODEL` is kept only for legacy configuration compatibility and is not the v0.4 routed model selector.

Current example defaults are `openai/gpt-oss-20b` for fast turns and `openai/gpt-oss-120b` for reasoning turns. Model IDs remain environment-configurable so the routing policy is not coupled to a provider's model lifecycle.
