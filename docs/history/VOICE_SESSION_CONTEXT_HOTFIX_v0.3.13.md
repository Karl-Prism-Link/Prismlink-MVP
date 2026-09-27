# PRISM LINK v0.3.13 — Live-call context hotfix

This hotfix restores the `LiveCallSession.recent_caller_transcripts` API required by the multi-turn date/time recovery logic in `VoiceToolset`.

It fixes runtime errors such as:

`'LiveCallSession' object has no attribute 'recent_caller_transcripts'`

The session now keeps a bounded in-memory list of finalized caller turns and exposes the latest turns through the `recent_caller_transcripts` property. This data is used only for bounded, deterministic voice-tool context recovery.
