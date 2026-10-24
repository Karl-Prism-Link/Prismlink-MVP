# Prism Link automated voice QA

This directory turns the 50 cases in the supplied **Testing pack.docx** into a stable input for an automated caller. The intended runner will use a dedicated Linphone SIP account to call a dedicated Prism Link test tenant, speak each scenario, listen and respond to the receptionist, then verify outcomes using tenant-scoped API reads.

## What is in this change

- `scenarios.json`: all 50 test IDs, category, priority, caller script, preconditions, expected behaviour, and pass criteria, transcribed from the testing pack.
- `tests/test_voice_qa_scenarios.py`: integrity checks so IDs and required acceptance criteria do not silently disappear.

This is the scenario registry and validation foundation. It does **not** yet place SIP calls. A call runner still needs a Linphone SDK adapter, audio injection/capture, speech recognition and synthesis, safe test-data setup/cleanup, and API assertions. The suite must report a case as blocked when one of those capabilities or required test fixtures is unavailable; it must never count a dry run as a pass.

## Intended execution flow

1. Select scenarios by ID, category, or release gate.
2. Reset only the dedicated test tenant to the fixture described by that scenario.
3. Place the outbound SIP call through Linphone.
4. Run the scenario as a constrained caller: follow its goal and scripted facts, respond to receptionist questions, and stop if the conversation leaves the allowed scenario.
5. Save the call ID, audio/transcript evidence, tool/API evidence, build and prompt versions.
6. Assert pass criteria from call events and tenant-scoped API state. Use `NEEDS_REVIEW` for subjective voice quality checks when no reliable metric exists.
7. Produce the pack's release summary and gate decision. Do not sign off a release automatically.

## Safety and isolation

- Configure a dedicated SIP account and test tenant; never point the runner at a customer number or production tenant.
- Restrict all setup, cleanup and assertion operations to the configured test tenant.
- The emergency and security scenarios are simulated conversations only. Never place real emergency calls.
- Do not run destructive cancellation/reschedule cases unless the fixture is isolated and can be reset.
- Keep credentials outside the repository. Require explicit environment configuration for SIP and API access.
- Preserve evidence according to the project's test-data policy; redact credentials and unrelated customer data.

## Run the registry checks

From the repository root:

```bash
python -m pytest -q tests/test_voice_qa_scenarios.py
```

The next implementation step is the Linphone call/audio adapter and API-backed assertions. Those require a dedicated test SIP credential and confirmation of which test tenant/number the runner may call.