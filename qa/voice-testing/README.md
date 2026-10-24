# Prism Link automated voice QA

This directory contains the 50 cases from **Testing pack.docx** and the first executable SIP test. T001 uses Linphone on the private test network to call Aurora directly, speak the supplied greeting, record the receptionist audio, transcribe it, and check the greeting against the test's pass criteria. No PSTN provider account is needed.

## T001 vertical slice

The runner sends “Hello.” as 8 kHz, 16-bit mono audio, captures the receptionist's audio, and transcribes it with the configured Deepgram key. It reports `PASS`, `FAIL`, `NEEDS_REVIEW`, or `BLOCKED` and saves WAV evidence plus a JSON result. Extra factual claims are sent for review rather than marked as a pass.

Run it on the test host after Linphone's Python SDK, `espeak-ng`, and `ffmpeg` are installed:

```bash
export LINPHONE_TARGET_URI='sip:qa@100.105.4.49:5060'
export DEEPGRAM_API_KEY='set-in-private-environment'
python qa/voice-testing/run_t001.py --dry-run
python qa/voice-testing/run_t001.py
```

Set the API key in a protected environment file on the runner, not in shell history or the repository. The Tailscale IP in the example is Aurora's known address; the runner refuses non-Tailscale targets. Dry run only validates the target and does not place a call.

The runner uses Linphone's file based media mode, so the headless host needs no microphone or speakers. Linphone's Python SDK documents outgoing SIP calls, periodic core iteration, and file based audio input/output. See the official [Python API](https://download.linphone.org/snapshots/docs/liblinphone/latest/python/).

## Network and safety requirements

Aurora's current SIP callback handles incoming INVITEs but does not authenticate a caller in that callback. Do not expose this test path to the public internet. Before using it, restrict SIP and RTP access at the host firewall to the runner's Tailscale address, and use Tailscale ACLs to limit which devices can reach Aurora. If this isolation cannot be confirmed, do not run the SIP test path.

- The test target must be a dedicated test tenant, never a customer tenant.
- T001 only greets; later destructive cases require isolated fixtures and reset support.
- Safety cases are simulated conversations only. Never place real emergency calls.
- Preserve evidence according to the project's test-data policy and redact unrelated customer data.

## Current scope

`scenarios.json` contains all test IDs, categories, priorities, caller scripts, preconditions, expected behaviour, and pass criteria. The registry and T001 checks run with:

```bash
python -m pytest -q tests/test_voice_qa_scenarios.py tests/test_voice_qa_t001.py
```

The Linphone call has not been run from this development environment. The next step is to install the runner dependencies on the isolated Tailscale host and verify the T001 audio path, then add conversational branches and API-backed assertions for booking tests.
