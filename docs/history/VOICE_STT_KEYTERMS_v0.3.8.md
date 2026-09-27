# PRISM LINK v0.3.8 — Salon STT Keyterm Prompting

This update improves recognition of configured salon vocabulary before speech reaches the LLM.

For Deepgram Nova-3, PRISM LINK now supplies tenant-specific keyterms built from:

- salon name
- active service names
- active staff names
- conservative apostrophe-free variants, e.g. `Men's Cut` and `Mens Cut`

This targets acoustic errors such as `men's cut` being transcribed as `mean cat` without teaching the LLM to guess around bad transcripts.

No environment changes or database migration are required. Restart the voice bot after applying the update so the current tenant's configured vocabulary is loaded into the Deepgram STT connection.
