# PRISM LINK monitoring metrics and alerting

**Updated:** 5 October 2026  
**Scope:** Aurora SIP/RTP, the PRISM LINK API and Pipecat voice pipelines, Google Calendar, database,
and host health.

Monitor the customer outcome as well as whether each process is running. A registered trunk does
not prove that the API accepted a call event, that RTP audio flowed in both directions, or that a
confirmed booking reached Google Calendar.

## Wallboard: first signals

| Signal | What to show | Initial alert |
|---|---|---|
| Aurora service | `prismlink-sip.service` state, uptime, restarts | Service stopped or repeatedly restarting |
| 2talk registration | Registered state and last successful registration time | Registration lost for more than 60 seconds |
| Inbound call events | Ringing events, API accepted/rejected counts, HTTP status | Any repeated 4xx/5xx callback failures; page on sustained failures |
| Active calls | Current calls versus configured Aurora and API capacity | Capacity reached or rejected calls increase |
| Pipecat readiness | Pipeline startup success/failure and startup duration | Startup failures or timeout |
| RTP/audio | RX/TX packet counts, last packet time, payload type, call duration | No RTP after answer, one-way audio, or unsupported codec |
| Booking outcome | Confirmed bookings, calendar write success/failure, duplicates | Any confirmed booking missing from calendar or duplicate write |
| Voice quality | Speech-end to first audio, STT/LLM/tool/TTS durations | Sustained latency above the pilot target |
| Host health | CPU, memory, disk, network, clock, service restarts | Disk nearly full, memory pressure, or API host unavailable |

Keep the wallboard glanceable: service state, trunk registration, live calls, callback errors, active
pipeline count, latest call outcome, and recent alerts. Keep detailed per-call diagnostics behind
the summary view.

## Per-call record

Correlate one internal `call_id` with Aurora's call ID, tenant, called/caller numbers, deployment
version, and timestamps for ringing, event accepted, pipeline ready, answer, first RTP in/out, and
call end. Record final SIP response or termination reason, RTP payload type, inbound/outbound packet
counts, stage durations, booking result, and calendar event ID when present.

Do not store raw audio or full telephone numbers in metrics labels. Apply the configured access,
retention, and consent policy to call details and transcripts.

## Aurora and SIP

Collect structured events from Aurora and the PRISM LINK callback/control API:

- Trunk registration transitions, registration age, and retry count.
- INVITE/ringing attempts, accepted/rejected calls, SIP response code, and setup duration.
- Callback delivery result, including HTTP status, validation failure, and response time.
- Active calls, configured capacity, capacity rejections, call duration, and end reason.
- RTP packets/bytes in each direction, last-packet age, payload type, and per-call packet loss or
  jitter when Aurora exposes those measurements.
- Call termination events and time taken to release each call's pipeline and sockets.

The API must alert on invalid call IDs and other rejected event payloads. A callback returning HTTP
success only confirms event delivery; track later pipeline acceptance and media readiness too.

## Pipecat and application

Measure pipeline startup duration and failure stage, active and completed sessions, disconnect
reason, and background-task cleanup. Record duration histograms for speech recognition, LLM
time-to-first-token and completion, tool execution, speech synthesis, and speech-end to first
outbound audio. Track provider timeouts, rate limits, authentication failures, and fallback replies.

Track tenant routing failures, call record persistence, transcript/summary persistence, booking
confirmation checks, appointment writes, calendar API errors, and idempotency/conflict rejections.
A successful booking must be reconciled against both the PRISM LINK database and Google Calendar.

## Host, services, and security

Monitor Aurora, PRISM LINK API, and database service state, process restarts, health/readiness checks,
CPU, memory, swap, disk/inodes, network errors, file descriptors, event-loop lag, time drift, and
backup/restore status. Collect systemd journal logs with timestamps and release identifiers.

Alert on repeated authentication failures, unexpected remote access to local control endpoints,
missing/invalid secrets, rate-limit spikes, cross-tenant access attempts, and unconfirmed booking
writes. Keep tokens and provider credentials out of logs and metric labels.

## Implementation order

1. Fix and test the Aurora event contract, including UUID validation and callback responses.
2. Add structured per-call events and stable counters, gauges, and latency histograms to Aurora and
   the PRISM LINK API.
3. Scrape application metrics and host metrics into Prometheus; build a Grafana wallboard for
   service state, registration, call flow, media, booking outcomes, and alerts.
4. Add alert routing for service outage, repeated callback failures, no/one-way audio, booking
   integrity failures, and resource pressure.
5. Rehearse call scenarios, provider outages, restarts, and restore procedures; tune thresholds from
   observed pilot traffic.

Use bounded metric labels such as service, outcome, codec, SIP response class, and failure stage.
Never label metrics with call IDs, tenant IDs, phone numbers, transcript text, or credentials.
