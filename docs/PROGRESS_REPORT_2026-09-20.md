# PRISM LINK progress report

**Reporting date:** 5 October 2026  
**Current stage:** Aurora integration validation and pre-pilot hardening

## Current state

The repository contains Aurora's SIP/RTP integration with the PRISM LINK API and an isolated
Pipecat voice pipeline per call. The dashboard, tenant-scoped booking workflows, calendar OAuth,
browser voice path, and Aurora setup documentation are present.

End-to-end telephone calling is not yet verified. Recent Aurora callback requests reached the API
but received HTTP 422 because the supplied call ID failed UUID validation. The next integration
check is to align Aurora's event payload and API schema, then verify call acceptance, bidirectional
PCMA/PCMU media, booking and Google Calendar delivery, and resource cleanup.

## Current architecture

```text
2talk SIP trunk
  -> Aurora SIP/RTP edge
  -> PRISM LINK internal SIP event callback
  -> per-call Pipecat pipeline and RTP socket
  -> tenant-scoped voice tools
  -> PRISM LINK database and Google Calendar
```

Aurora's control API and the internal callback are intended to remain on loopback. Keep control
tokens and provider credentials in private service environments.

## Readiness checks

- Confirm Aurora sends a UUID call ID accepted by the PRISM LINK callback schema.
- Confirm the pipeline becomes ready before Aurora answers.
- Confirm PCMA/PCMU audio flows in both directions and calls release sockets and tasks on hang-up.
- Confirm tenant selection uses the called number and unknown numbers fail closed.
- Confirm a confirmed appointment appears once in PRISM LINK and Google Calendar.
- Exercise enquiries, booking, rescheduling, cancellation, messages, after-hours behavior, caller
  interruption, provider failures, and concurrent calls.
- Add service, callback, pipeline, RTP, calendar, and host alerts before a controlled pilot.

## Current evidence

Browser voice and the booking safeguards are implemented. Aurora integration code and RTP codec
helpers are present with automated tests. The live callback validation error means the telephone
path and real Google Calendar booking outcome still need an end-to-end test. Do not treat this
report as a completed pilot sign-off.
