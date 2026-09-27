# PRISM LINK monitoring metrics and alerting specification

**Research date:** 20 September 2026  
**Scope:** salon-first AI voice receptionist SaaS using 2talk, FreeSWITCH, Pipecat,
Deepgram, Groq, Google Calendar, and the PRISM LINK application/database

## Executive recommendation

PRISM LINK must monitor whether it delivered a correct customer outcome, not merely whether the
server stayed online. A call can be answered successfully while the caller hears silence, waits too
long, receives an incorrect answer, or gets the wrong appointment. The production view therefore
needs six layers:

1. **Customer outcome** — was the enquiry, message, or appointment task completed correctly?
2. **Conversation experience** — was the exchange fast, intelligible, and free of dead air?
3. **Telephony and media** — did SIP connect and did RTP audio flow in both directions?
4. **AI and workflow dependencies** — did STT, LLM, TTS, and Calendar calls work?
5. **Platform health** — were the services and microserver healthy and within capacity?
6. **Safety, privacy, and cost** — did the system remain authorised, compliant, isolated, and
   economical?

For the first salon pilot, build a small control panel around the twelve metrics below before
building broad analytics.

## Pilot control panel: the twelve metrics that matter first

| Metric | Definition | Why it matters | Proposed pilot target |
|---|---|---|---|
| Inbound connection rate | Calls answered by FreeSWITCH / inbound call attempts | Detects trunk, routing, and dialplan failure | At least 98% excluding caller abandonment |
| AI session start rate | Calls that create a working PRISM LINK pipeline / answered calls | Separates SIP success from bot startup success | At least 99% |
| Audible greeting rate | Calls where outbound bot audio starts / AI sessions | Detects the silent-call and one-way-audio class | At least 99%; any confirmed failure is urgent |
| Successful call outcome rate | Calls ending in a valid resolved, message, appointment, or safe-follow-up outcome / AI sessions | Primary service reliability measure | At least 95% during controlled pilot |
| Booking workflow success | Correctly confirmed booking/reschedule/cancel operations / valid attempts | Measures the highest-risk business workflow | At least 98%; no false success claims |
| Booking integrity exceptions | Duplicate, wrong-tenant, wrong-time, unconfirmed, or Calendar/database mismatch events | A single bad write can harm trust | Zero |
| User-to-bot response latency | End of caller speech to first audible bot response, p50/p95/p99 | Best measure of conversational responsiveness | Establish baseline; initial p95 warning at 2.5 s |
| Dead-air call rate | Calls with answer-to-greeting or mid-call silence above the allowed interval | Finds failures that HTTP health checks miss | Below 1%; investigate every pilot occurrence |
| Provider error rate | Failed/time-out/rate-limited STT, LLM, TTS, or Calendar operations / operations | Identifies the failing dependency | Below 1% per provider; urgent above 5% |
| Abnormal disconnect rate | Calls not ending in a normal or caller-initiated completion / connected calls | Detects crashes, timeouts, and dropped media | Below 1% |
| Cost per successful outcome | Total telecom + AI + infrastructure cost / successful outcomes | Shows whether the SaaS is commercially viable | Baseline first, then budget per plan |
| Privacy and security exceptions | Retention breaches, missing recording notice, cross-tenant access, unauthorised writes, or exposed secrets | These are guardrails, not optimisation metrics | Zero |

The targets above are **proposed starting values**, not proven baselines. Pilot traffic will be low,
so alert on individual serious events as well as percentages. Recalibrate latency and availability
targets after at least 100 representative calls.

## 1. Customer outcome and salon value

These metrics belong at the top of the dashboard because they answer whether PRISM LINK is useful.

### Call outcomes

- Total inbound calls, connected calls, and AI-handled calls.
- Outcomes by type: `enquiry_resolved`, `booking_created`, `booking_rescheduled`,
  `booking_cancelled`, `message_taken`, `staff_follow_up`, `caller_abandoned`, `failed_fallback`,
  and `unknown`.
- Successful outcome rate and unresolved-call rate.
- First-call resolution proxy: callers who do not call again about the same task within 24 hours.
- Repeat-call rate within 24 hours and seven days.
- Calls abandoned before greeting, during greeting, and during a workflow.
- Follow-up-required count and age, including messages not acknowledged within the salon's target.
- Human-transfer rate when transfer is introduced; distinguish requested transfers from AI failure.

### Appointment workflow integrity

- Availability checks attempted, successful, unavailable, and failed.
- Booking, reschedule, and cancellation attempts and confirmed completions.
- Explicit-confirmation coverage. Every consequential write must have a fresh confirmation.
- False-success count: the assistant said an action succeeded but the authoritative tool did not.
- Duplicate booking attempts prevented and duplicate bookings actually created.
- Calendar API write/read error counts by code, including `401`, `403`, `409`, `429`, and `5xx`.
- Calendar operation duration and retry count.
- PRISM LINK database versus Google Calendar reconciliation mismatch count.
- Orphan database appointment and orphan Calendar event count.
- Wrong-tenant, wrong-customer, wrong-service, or wrong-time write count.
- Booking corrections or reversals within 24 hours as a quality signal.

Google Calendar can return quota errors as either `403` or `429`, and Google recommends testing
quota handling and exponential backoff in a real environment. Monitor both the error reason and
retry outcome rather than grouping every `4xx` together.

### Salon business value

- Missed or after-hours calls handled.
- Appointments created, moved, or cancelled by PRISM LINK.
- Messages captured with a callback number and usable content.
- Estimated staff minutes saved, using a documented assumption rather than presenting it as direct
  observation.
- Estimated appointment value created or retained. Keep this separate from recognised revenue.
- Outcome rate and value by salon, time of day, call intent, and acquisition cohort.

## 2. Conversation experience and AI quality

### End-to-end turn latency

Measure from **end of caller speech to first audible bot audio** for every turn, then publish p50,
p95, and p99 by tenant and provider/model combination. Retain a per-call trace for diagnosis.

Break each turn into:

- voice-activity endpointing delay;
- STT final-transcript latency;
- routing and deterministic-state processing;
- LLM time to first token and total generation time, when an LLM is used;
- tool-call duration, including Calendar/database work;
- TTS time to first audio byte;
- buffering/transport delay until audio is sent to FreeSWITCH; and
- total caller-speech-end to first-audible-audio latency.

This matches Pipecat's existing time-to-first-byte and processing metrics and PRISM LINK's existing
`UserBotLatencyObserver`. Deepgram likewise recommends measuring total latency and its network,
TTFB, and synthesis components. Groq distinguishes queue latency, time to first token, and full
end-to-end generation latency.

### Speech-to-text

- Connection success, disconnect, reconnect, and connection-error counts.
- First interim and final transcript latency.
- Empty-final-transcript and no-speech-finalisation rate.
- Utterance duration and endpointing delay.
- Confidence distribution where the selected Deepgram model returns confidence.
- Correction rate for names, phone numbers, service names, dates, and times.
- Word error rate on a small, consented, human-labelled evaluation sample—not inferred from live
  transcripts alone.
- Language/model/endpointing configuration used on each call.
- Provider request failures, authentication failures, timeouts, and rate-limit responses.

### LLM and deterministic routing

- LLM requests, success/error/timeout/rate-limit counts, and retry count.
- Time to first token, total duration, input tokens, output tokens, and model selected.
- Deterministic fast-path rate versus LLM-path rate by intent.
- Tool calls by tool and result code.
- Invalid tool arguments, schema validation failures, and rejected unsafe/unconfirmed writes.
- Context truncation count and context size.
- Fallback response count and repeated-fallback-loop count.
- Intent classification changes and unresolved/unknown intent rate.
- Prompt-injection or policy-violation attempts blocked.
- Unauthorised tool execution and cross-tenant tool access: target zero.

OpenTelemetry's GenAI conventions provide standard names for operation-duration and token-usage
telemetry. Groq also exposes request status, token, queue latency, time-to-first-token, and
end-to-end latency metrics, so local client-side metrics can be compared with provider-side data.

### Text-to-speech and playback

- TTS request success, connection failure, reconnect, timeout, and retry counts.
- Text-to-first-audio-byte latency and total synthesis duration.
- Characters or audio seconds generated.
- Synthesised-audio duration versus wall-clock generation time.
- First bot audio sent and first playback event after answer.
- Empty audio frames, under-runs, playback interruption, and duplicate-speech suppression count.
- Voice/model/encoding/sample-rate configuration used.

### Natural conversation behaviour

- Answer-to-greeting latency.
- Silence intervals and maximum dead-air duration per call.
- Barge-in attempts, detection latency, successful interruption, and false interruption rate.
- Caller/bot overlap duration.
- Repeated question rate.
- Average turns and duration by successful outcome.
- Very short calls, unusually long calls, and loops detected.
- Human-reviewed quality sample: correctness, helpfulness, naturalness, concise speech, policy
  compliance, and transcript accuracy.
- Caller complaints and optional post-call satisfaction. Treat automated sentiment as a diagnostic
  signal, not ground truth.

## 3. Telephony, SIP, and RTP media

SIP signalling and RTP media must be measured independently. FreeSWITCH documents that a call can
be fully connected while one or both media directions are silent.

### Trunk and call setup

- 2talk gateway registration state, time since last successful registration, failed registration
  count, and OPTIONS/ping response latency.
- Inbound `INVITE` count and final SIP response code.
- Answer rate and answer-seizure ratio.
- Post-dial delay and answer setup duration.
- FreeSWITCH `Hangup-Cause`, `sip_term_status`, `proto_specific_hangup_cause`, and
  `originate_disposition` distributions.
- Who disconnected: caller, PRISM LINK, provider, network timeout, or unknown.
- Active channels, calls per second, concurrent calls, rejected calls at `FS_MAX_CALLS`, and peak
  concurrency.
- Call duration and billable seconds distributions.
- Unknown DID and tenant-routing failure count.

FreeSWITCH emits channel lifecycle events with a `Unique-ID`/call UUID and exposes Q.850 hangup
causes, making those suitable for the canonical telephony event stream.

### RTP and audio quality

- Inbound/outbound RTP packet and byte counts.
- Inbound/outbound packet-loss ratio and skipped packet count.
- Average and maximum jitter.
- Round-trip time where RTCP/provider data makes it available.
- Mean Opinion Score (MOS) where it can be calculated or obtained.
- Selected codec, sample rate, packetisation time, and transcoding path.
- RTP start delay after answer.
- Audio energy/level in each direction and consecutive zero-audio duration.
- One-way-audio and no-audio calls.
- Media timeout and `MEDIA_TIMEOUT` hangups.

FreeSWITCH exposes `rtp_audio_*` channel variables for packet, byte, jitter, skip, and codec data,
and those can be captured in the CDR or at hangup. As directional starting points—not 2talk-specific
SLAs—Twilio marks packet loss above 5%, maximum jitter above 30 ms, MOS below 3.5, and RTT above
400 ms as degraded call-quality conditions. Use stricter warning thresholds once PRISM LINK has a
clean baseline.

### WebSocket audio bridge

- FreeSWITCH-to-Pipecat WebSocket connection attempts, accepted sessions, rejected/auth-failed
  sessions, and active sessions.
- Connection duration, unexpected closure, receive timeout, send timeout, and reconnect counts.
- Input and output audio frames/bytes and queue depth.
- Dropped, late, malformed, or wrong-sample-rate frames.
- Time from call answer to WebSocket open and from open to pipeline ready.
- Cleanup exceptions after disconnect, tracked separately from caller-impacting failures.

## 4. Platform, dependency, and capacity health

Google's Site Reliability Engineering guidance recommends monitoring latency, traffic, errors, and
saturation—the four golden signals. Apply them to every service and dependency.

### Services and host

- `prism-link-voice`, FreeSWITCH, API, and database up/down state.
- Process uptime, restart count, exit code, crash-loop state, and deployment version/commit.
- Health/live/ready probe results and probe latency.
- CPU, load average, memory, swap, disk use, inode use, disk I/O, and network errors.
- File-descriptor, socket, thread/task, and event-loop-lag measurements.
- Host time drift and TLS certificate expiry.
- FreeSWITCH heartbeat, session count, sessions per second, and idle CPU.
- Backup age, backup success, and last proven restore date.

### Database and queues

- Query/transaction latency, errors, rollbacks, connection-pool use, and lock/busy errors.
- Database file/storage growth.
- In-progress calls that never reach a terminal state.
- New messages and follow-ups waiting beyond target age.
- Reconciliation backlog and retry/dead-letter backlog when background jobs are introduced.

### External dependencies

For Deepgram STT/TTS, Groq, Google Calendar, and 2talk, track:

- request/operation volume;
- availability and error code/reason;
- p50/p95/p99 client-side latency;
- provider-reported latency where available;
- timeout, retry, reconnect, and fallback rate;
- remaining rate-limit/quota values where exposed; and
- detected provider incident duration.

Pipecat service events expose STT/TTS connected, disconnected, and connection-error events, and its
pipeline heartbeat can detect a processor stall. Enable and collect those signals rather than
parsing only free-form log text.

## 5. Security, tenant isolation, and privacy

These metrics are primarily exception counters. Several should remain exactly zero.

- Authentication failures by interface and source category.
- Rejected SIP/WebSocket connections and unexpected source IPs.
- Rate-limit blocks and scanning/brute-force patterns.
- FreeSWITCH event-socket authentication failures.
- Secret missing/invalid events and credential rotation due dates.
- Cross-tenant access attempts, prevented access, and confirmed cross-tenant disclosure/write.
- Unconfirmed booking/reschedule/cancel attempts rejected by the safety layer.
- Prompt-injection/policy-bypass attempts and unsafe tool calls blocked.
- Recording/transcript notice delivered before collection, if calls are recorded.
- Calls recorded without the required notice or policy state: target zero.
- Recordings/transcripts beyond their retention deadline: target zero.
- Access, correction, export, and deletion requests received, due, completed, or failed.
- Audit-event delivery gaps and audit-log integrity failures.
- Raw phone numbers, caller names, transcripts, or credentials detected in metric labels: target
  zero.

New Zealand's Privacy Commissioner advises being upfront before recording a client and states that
personal information must not be retained longer than required for its lawful purpose. Monitoring
should therefore prove that the chosen notice and retention controls actually ran; it should not
silently collect more personal information for observability.

## 6. Usage, capacity, and unit economics

- Calls and connected minutes by tenant and plan.
- Peak concurrent calls and capacity rejected/queued.
- Deepgram STT audio seconds and TTS characters/audio seconds.
- Groq requests plus input/output tokens by model and route.
- Google Calendar requests, retries, and quota consumption.
- 2talk minutes and number/trunk costs.
- Host, storage, backup, and observability costs.
- Total cost per call, connected minute, successful outcome, and completed appointment workflow.
- Gross margin estimate per tenant/plan.
- Daily/monthly budget burn and cost anomaly versus rolling baseline.
- Tenant usage approaching plan limit and provider usage approaching quota.

Do not hard-code current provider price tables into telemetry. Store provider quantities and apply a
versioned price configuration so historical costs remain reproducible when prices change.

## Proposed alerts for the controlled pilot

| Severity | Trigger | Initial rule |
|---|---|---|
| Page now | Trunk or core voice runtime unavailable | Gateway not `REGED/UP`, FreeSWITCH down, or `prism-link-voice` down for more than 60 seconds |
| Page now | Booking safety/integrity breach | Any false success, duplicate write, cross-tenant write, unconfirmed write, or reconciliation mismatch |
| Page now | Confirmed silent/one-way call | Any pilot call with signalling success but missing sustained audio in one direction |
| Urgent | Calls arrive but are not handled | Two pipeline-start failures or unanswered system calls within 10 minutes |
| Urgent | Provider failure | Error/timeout/rate-limit rate above 5% over 10 minutes with at least 10 operations, or two consecutive pilot-call failures |
| Urgent | Conversation stalls | Pipeline heartbeat missing, or dead air above 10 seconds while the call remains active |
| Warning | Latency regression | p95 user-to-bot latency above 2.5 seconds for 15 minutes; critical above 4 seconds |
| Warning | Media degradation | Packet loss above 5%, maximum jitter above 30 ms, MOS below 3.5, or RTT above 400 ms |
| Warning | Host capacity | Disk above 80%, memory above 85%, or sustained CPU/load saturation; critical at 90% disk |
| Warning | Crash loop | More than one unexpected service restart in 15 minutes |
| Ticket | Follow-up backlog | Any urgent message beyond its target age or normal follow-up older than one business day |
| Ticket | Cost anomaly | Daily cost or cost per successful outcome more than 20% above the established rolling baseline |
| Ticket | Privacy lifecycle | Any item past retention/deletion deadline or failed privacy request workflow |

Percentage alerts require a minimum event count. With low pilot volume, also notify on consecutive
failures and on every integrity/privacy breach.

## Dashboard layout

### 1. Pilot overview

Calls attempted, connected, AI sessions, successful outcomes, appointment outcomes, messages,
follow-up backlog, bad calls, p95 response latency, and cost per successful outcome.

### 2. Live operations

Trunk state, active calls, service status/restarts, pipeline starts, WebSocket sessions, provider
availability/errors, host saturation, and current alerts.

### 3. Call quality

Connection and answer rates, post-dial delay, hangup causes, packet loss, jitter, RTT, MOS, codecs,
one-way/no-audio calls, and media timeouts.

### 4. AI performance

User-to-bot latency and stage breakdown, STT/TTS connections, LLM TTFT, tokens, deterministic versus
LLM routes, tool duration, fallbacks, dead air, barge-in, and quality-review results.

### 5. Workflow integrity

Appointment/message outcomes, explicit confirmations, rejected unsafe actions, Calendar errors,
retries, reconciliation mismatches, and follow-up age.

### 6. Security, privacy, and cost

Authentication/rate-limit events, tenant-isolation exceptions, notice/retention status, provider
usage and quotas, cost by tenant/provider, and anomaly alerts.

Every dashboard must link from an aggregate point to a **per-call timeline** that correlates SIP,
RTP, WebSocket, STT, routing, LLM, tool, TTS, and outcome events.

## Telemetry design

### Per-call correlation record

At minimum, persist or trace:

- internal `call_id`, FreeSWITCH call UUID, and external provider call ID;
- internal `tenant_id`, environment, release/commit, direction, and called-number route;
- start, ring, answer, WebSocket open, pipeline ready, greeting audio, and end timestamps;
- final SIP/Q.850 cause and disconnect initiator;
- codec and aggregate RTP statistics;
- AI provider/model/configuration and stage durations;
- tool calls and authoritative result codes;
- final intent, outcome, follow-up state, and appointment/message reference;
- provider usage quantities and calculated cost; and
- consent/recording/retention policy state without copying sensitive content into metrics.

### Labels and privacy

Use low-cardinality metric labels such as `environment`, `provider`, `model`, `operation`, `result`,
`intent`, and `outcome`. Put `call_id`, raw `tenant_id`, phone numbers, names, appointment IDs, and
transcript content in protected traces/logs or relational records, not Prometheus labels.
Prometheus explicitly warns against high-cardinality labels such as user IDs and email addresses;
the same rule applies to callers and calls.

### Metric types

- **Counters:** calls, errors, retries, tool outcomes, tokens, audio seconds, bytes, and safety
  exceptions.
- **Histograms:** call/turn/stage duration, packet loss, jitter, tool latency, and cost.
- **Gauges:** active calls, gateway state, queue depth, disk/memory use, follow-up backlog, and quota
  remaining.
- **Events/traces:** one per call lifecycle or exceptional action, correlated by call ID.

## What the repository already has

- `UserBotLatencyObserver` logs user-to-bot latency and a per-service breakdown.
- Pipecat pipeline metrics and usage metrics are enabled in the main bot.
- Calls already store an external call ID, status, outcome, timestamps, duration, transcript,
  summary, and follow-up state.
- Health, liveness, and readiness routes exist.
- FreeSWITCH transport exposes connection metadata and isolates calls by session.
- Booking tools enforce explicit confirmation and tenant-scoped writes.

These are useful raw signals, but they are currently log/database records rather than a complete
metrics, tracing, alerting, and reconciliation system.

## Recommended implementation sequence

### Phase 1 — before wider live calling

1. Emit structured JSON events with `call_id`, FreeSWITCH UUID, tenant key, stage, operation,
   duration, result, and release version.
2. Add a Prometheus endpoint for counters, gauges, and histograms; run Prometheus and Grafana locally
   or use a low-cost hosted equivalent.
3. Collect systemd/node health plus FreeSWITCH heartbeat, channel, hangup-cause, CDR, gateway, and
   `rtp_audio_*` data through ESL/CDR hooks.
4. Convert existing Pipecat latency/usage callbacks and service connection events into metrics.
5. Create per-call outcome/integrity events and a daily database/Calendar reconciliation check.
6. Implement the page-now alerts above and one per-call diagnostic view.

### Phase 2 — controlled salon pilot

1. Establish latency, call-quality, outcome, and cost baselines over at least 100 representative
   calls.
2. Human-review a consented random sample plus every failed, very slow, or corrected call.
3. Add STT field-correction measures and workflow-level quality scoring.
4. Add retention/deletion enforcement reports and backup-restore evidence.
5. Recalibrate alerts and set a formal pilot SLO with an error budget.

### Phase 3 — multi-tenant SaaS growth

1. Per-tenant SLOs, quotas, billing quantities, activation funnel, and margin reporting.
2. Capacity forecasting and load tests for concurrent calls.
3. Automated anomaly detection for failure, latency, fraud/abuse, and spend.
4. Provider/model comparison using outcome quality, latency, and total cost—not speed alone.

## Authoritative sources

- [Google SRE: Monitoring Distributed Systems](https://sre.google/sre-book/monitoring-distributed-systems/)
  — latency, traffic, errors, and saturation.
- [FreeSWITCH call-setup failures](https://developer.signalwire.com/freeswitch/troubleshooting/call-setup/)
  — Q.850/SIP hangup causes.
- [FreeSWITCH channel variables](https://developer.signalwire.com/freeswitch/reference/channel-variables/)
  — RTP packet, byte, jitter, skip, and codec statistics.
- [FreeSWITCH no-audio and one-way-audio guidance](https://developer.signalwire.com/freeswitch/troubleshooting/audio/)
  — signalling/media independence and media timeouts.
- [FreeSWITCH events catalog](https://developer.signalwire.com/freeswitch/programming/events-catalog/)
  — channel lifecycle, media, registration, and heartbeat events.
- [Pipecat metrics integration](https://docs.pipecat.ai/server/services/analytics/sentry) and
  [service events](https://docs.pipecat.ai/server/utilities/service-events) — TTFB, processing,
  connection, error, timeout, and tool-call events.
- [Pipecat pipeline heartbeats](https://docs.pipecat.ai/api-reference/server/pipeline/heartbeats) —
  detecting pipeline stalls.
- [Deepgram TTS latency](https://developers.deepgram.com/docs/text-to-speech-latency) — total,
  network, TTFB, and synthesis latency.
- [Groq Prometheus metrics](https://console.groq.com/docs/prometheus-metrics),
  [rate limits](https://console.groq.com/docs/rate-limits), and
  [production checklist](https://console.groq.com/docs/production-readiness/production-ready-checklist)
  — request, token, latency, quota, error, and cost monitoring.
- [OpenTelemetry GenAI observability](https://opentelemetry.io/blog/2026/genai-observability/) —
  standard operation-duration and token-usage telemetry.
- [Google Calendar quota guidance](https://developers.google.com/workspace/calendar/api/guides/quota)
  and [Calendar API errors](https://developers.google.com/workspace/calendar/api/guides/errors) —
  quota errors, retries, and exponential backoff.
- [Prometheus instrumentation guidance](https://prometheus.io/docs/practices/instrumentation/) and
  [metric naming](https://prometheus.io/docs/practices/naming/) — metric design and label
  cardinality.
- [New Zealand Privacy Commissioner: recording clients](https://www.privacy.org.nz/resources-and-learning/knowledge-base/view/325/)
  and [Privacy Principle 9](https://www.privacy.org.nz/privacy-principles/9/) — notice and retention.

