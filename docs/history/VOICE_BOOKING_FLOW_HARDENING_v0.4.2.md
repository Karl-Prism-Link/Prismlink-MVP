# PRISM LINK Voice Booking Flow Hardening v0.4.2

This release reduces LLM dependence in the booking path after live tests exposed repeated questions, punctuation-only TTS fragments, STT service confusions, and a caller name correction being sent back to Groq.

Changes:
- resolve configured service names before the LLM, including conservative known STT confusions such as `beans cut`, `meme's cut`, and `mean cat` -> configured Men's Cut;
- exact configured custom services win before generic STT-confusion aliases;
- remember a caller-spoken weekday and ask for the missing time deterministically when service + day are already known;
- accept an explicitly spelled/corrected booking name while collecting the phone number or after the confirmation readback;
- re-prepare the booking deterministically after a name correction, preserving the caller-spoken phone digits;
- suppress punctuation-only assistant fragments;
- collapse semantically equivalent time questions and emit at most one assistant question per caller turn.

Correctness remains in the deterministic tool layer. Google Calendar availability and writes are unchanged.
