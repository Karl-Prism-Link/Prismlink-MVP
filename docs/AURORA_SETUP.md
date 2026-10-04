# Aurora SIP and Pipecat integration

Aurora can deliver registered-trunk SIP calls into the PRISM LINK live voice
pipeline. The callback and both control APIs are intended to run on the same
Linux host and remain bound to loopback. Aurora forwards PCMA/PCMU RTP to a
per-call UDP socket created by the Pipecat API process; each call gets an
isolated Pipecat pipeline.

## Requirements

- Aurora must be registered to 2talk and send `call.ringing` events to
  `http://127.0.0.1:8000/internal/sip-events`.
- The PRISM LINK API process must include the `voice` extra and run on the same
  host as Aurora.
- The API's `DEEPGRAM_API_KEY`, selected LLM provider key, and tenant
  configuration must be valid.
- The number in Aurora's called SIP URI should match the salon's configured
  phone number. If it does not, set `VOICE_TENANT_SLUG` as the explicit fallback.
- Aurora's inbound offer must use PCMA (payload 8) or PCMU (payload 0), the
  codecs supported by Aurora's current no-transcode RTP bridge.

## Environment

Add these values to the PRISM LINK API process environment (normally its private
`.env` file):

```dotenv
AURORA_CONTROL_API_URL=http://127.0.0.1:8088
AURORA_CONTROL_API_TOKEN=the-same-secret-as-PRISMLINK_API_TOKEN-in-Aurora
AURORA_RTP_HOST=192.168.1.26
AURORA_RTP_BIND_HOST=127.0.0.1
AURORA_STARTUP_TIMEOUT_SECONDS=20
AURORA_MAX_ACTIVE_CALLS=8
```

Set `AURORA_RTP_HOST` to Aurora's `local_ip` from its settings file. It must be
the local interface address on which Aurora binds its RTP proxy, not the public
`external_ip`. The Pipecat RTP socket binds to `AURORA_RTP_BIND_HOST` (loopback
by default) and uses a dynamically allocated UDP port for each call. The PRISM
LINK API is still bound to `127.0.0.1:8000`, and the Aurora control API stays at
`127.0.0.1:8088`.

Keep the shared control token private. Do not put it in source control, paste it
into chat, or expose Aurora's control API to the Internet. Aurora callbacks have
no signature header, so the receiving API must remain loopback-only.

## Call flow

1. Aurora posts a `call.ringing` event to `/internal/sip-events`.
2. PRISM LINK validates the call ID, PCMA/PCMU payload type, and allocated RTP
   ports, then starts a Pipecat pipeline for that call.
3. When the pipeline's input and output are ready, PRISM LINK calls Aurora's
   authenticated `/v1/calls/{call_id}/accept` endpoint with the local Pipecat RTP
   socket address.
4. Aurora answers the SIP call and begins forwarding RTP. PRISM LINK decodes
   G.711 to 8 kHz PCM for Pipecat and encodes Pipecat audio back to G.711 RTP.
5. Aurora's `call.ended`, `call.cancel_received`, or `call.bye_received` event
   stops the associated pipeline and releases its UDP socket.

The callback returns quickly and starts the pipeline in the background. If
Pipecat is not ready or cannot start, PRISM LINK asks Aurora to reject the call
with SIP 503. The webhook is an internal transport hook; it does not create a
browser-authenticated dashboard call.

## Validation

After adding the environment values, restart the PRISM LINK API process, then
call the 2talk pilot number. Expected results:

- Aurora logs a `call.ringing` event and then `200 OK sent` after Pipecat starts.
- PRISM LINK logs an Aurora call start and the usual live voice pipeline logs.
- Ending the call produces `call.ended` and releases the RTP socket.

This bridge does not transcode, record, mix, or support WebRTC/SRTP. Audio is
limited to mono 8 kHz PCMA/PCMU. Verify bidirectional audio and call cleanup
before using it with customers.
