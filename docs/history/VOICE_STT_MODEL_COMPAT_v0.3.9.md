# PRISM LINK Voice STT Model Compatibility v0.3.9

This update makes tenant vocabulary prompting compatible with the selected Deepgram STT model.

- Nova-3: uses `keyterm` with configured salon/service/staff phrases.
- Nova-2 (and older Nova/Enhanced/Base families): uses `keywords` with conservative individual-word boosts.
- Unknown model families: sends no vocabulary hint rather than risking a rejected Deepgram WebSocket handshake.

This fixes the Nova-2 startup failure caused by sending Nova-3-only `keyterm` parameters.
