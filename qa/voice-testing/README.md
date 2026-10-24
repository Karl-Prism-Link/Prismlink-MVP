# Prism Link automated voice QA

This directory turns the 50 cases in the supplied **Testing pack.docx** into a stable input for an automated caller. The planned runner will use Linphone on the private test network to send a SIP call directly to Aurora, speak each scenario, listen and respond to the receptionist, then verify outcomes using tenant-scoped API reads. This avoids a separate PSTN provider and does not use 2talk to originate the test calls.

## What is in this change

- `scenarios.json`: all 50 test IDs, category, priority, caller script, preconditions, expected behaviour, and pass criteria, transcribed from the testing pack.
- `tests/test_voice_qa_scenarios.py`: integrity checks so IDs and required acceptance criteria do not silently disappear.

This is the scenario registry and validation foundation. It does **not** yet place SIP calls. A call runner still needs a Linphone SDK adapter, audio injection/capture, speech recognition and synthesis, safe test-data setup/cleanup, and API assertions. The suite must report a case as blocked when one of those capabilities or required test fixtures is unavailable; it must never count a dry run as a pass.

## Intended execution flow

1. Select scenarios by ID, category, or release gate.
2. Reset only the dedicated test tenant to the fixture described by that scenario.
3. Place a direct SIP call from Linphone to Aurora's Tailscale address and SIP port.
4. Run the scenario as a constrained caller: follow its goal and scripted facts, respond to receptionist questions, and stop if the conversation leaves the allowed scenario.
5. Save the call ID, audio/transcript evidence, tool/API evidence, build and prompt versions.
6. Assert pass criteria from call events and tenant-scoped API state. Use `NEEDS_REVIEW` for subjective voice quality checks when no reliable metric exists.
7. Produce the pack's release summary and gate decision. Do not sign off a release automatically.

## Network and safety requirements

Aurora's current SIP callback handles incoming INVITEs but does not authenticate a caller in that callback. Do not expose this test path to the public internet. Before using it, restrict SIP and RTP access at the host firewall to the test runner's Tailscale address, and use Tailscale ACLs to limit which devices can reach Aurora. If that isolation cannot be confirmed, do not run the SIP test path.

- The test target must be a dedicated test tenant, never a customer tenant.
- Restrict setup, cleanup and assertion operations to the configured test tenant.
- The emergency and security scenarios are simulated conversations only. Never place real emergency calls.
- Do not run destructive cancellation/reschedule cases unless the fixture is isolated and can be reset.
- Keep API secrets outside the repository. Direct SIP testing does not require a PSTN account; API credentials still must be configured privately.
- Preserve evidence according to the project's test-data policy; redact credentials and unrelated customer data.

## Run the registry checks

From the repository root:

```bash
python -m pytest -q tests/test_voice_qa_scenarios.py
```

The next implementation step is the Linphone call/audio adapter and API-backed assertions. This route needs the test runner's Tailscale address and a confirmed test tenant/API credential, but no separate phone provider account.