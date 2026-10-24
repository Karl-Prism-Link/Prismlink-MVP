# Prism Link automated voice QA

This directory contains the 50 cases from **Testing pack.docx** and the first executable SIP test. T001 uses Linphone on the private test network to call Aurora directly, speak the supplied greeting, record the receptionist audio, transcribe it, and check the greeting against the test's pass criteria. No PSTN provider account is needed.

## Call path

Linphone sends a SIP INVITE directly to Aurora's Tailscale address on UDP 5060. Aurora posts
the call event to the Prism Link API, which resolves Luxe Hair & Beauty by its called number,
starts the Pipecat voice pipeline, and returns the RTP destination to Aurora. This direct SIP call
does not use the 2talk trunk or PSTN.

```text
Linphone QA runner -> Aurora SIP/RTP -> Prism Link API -> Pipecat
```

The Aurora host must accept SIP and RTP from the QA runner over Tailscale only. The API must be
configured with the dedicated QA data and its Aurora callback/control tokens and Pipecat provider
settings. The T001 target is pinned to Luxe Hair & Beauty's assigned number, `02825523645`.
The API looks up the tenant by the called number; keep the QA data isolated and do not change the
live API's fallback tenant setting for this test.

## T001 vertical slice

The runner sends “Hello.” as 8 kHz, 16-bit mono audio, captures the receptionist's audio, and transcribes it with the configured Deepgram key. It reports `PASS`, `FAIL`, `NEEDS_REVIEW`, or `BLOCKED` and saves WAV evidence plus a JSON result. Extra factual claims are sent for review rather than marked as a pass.

Run it on the test host after Linphone's Python SDK, `espeak-ng`, and `ffmpeg` are installed:

```bash
export LINPHONE_TARGET_URI='sip:02825523645@100.105.4.49:5060'
set -a
. /etc/prism-link-voice-qa.env
set +a
python qa/voice-testing/run_t001.py --dry-run
python qa/voice-testing/run_t001.py
```

Store `DEEPGRAM_API_KEY` in `/etc/prism-link-voice-qa.env` with owner-only read permissions; do not put it in shell history or the repository. The Tailscale IP in the example is Aurora's known address; the runner refuses non-Tailscale targets. Dry run only validates the target and does not place a call.

The runner uses Linphone's file based media mode, so the headless host needs no microphone or speakers. Linphone's Python SDK documents outgoing SIP calls, periodic core iteration, and file based audio input/output. See the official [Python API](https://download.linphone.org/snapshots/docs/liblinphone/latest/python/).

## Manual systemd runner

Install `espeak-ng` and `ffmpeg`, then install the repository at `/opt/prism-link-voice-qa`.
Create `/opt/prism-link-voice-qa/.venv` and install an official Linphone Python SDK wheel that
matches the host's Python version and architecture. The wheel is published by Linphone; the
Python wrapper supports Linux x86_64. Create `/etc/prism-link-voice-qa.env` with only
`DEEPGRAM_API_KEY`, owned by `root:prismlink` and mode `0640`. Create the results directory
owned by `prismlink` with mode `0700`, then install the unit:

```bash
sudo install -d -o prismlink -g prismlink -m 0700 /var/lib/prism-link-voice-qa/results
sudo install -m 0644 qa/voice-testing/prism-link-voice-qa.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start prism-link-voice-qa.service
sudo journalctl -u prism-link-voice-qa.service -n 80 --no-pager
```

This is a manual, one-shot unit; it does not start automatically at boot. It calls only
`02825523645` on Aurora's Tailscale address. Check the API uses the isolated QA database and
that Aurora's SIP/RTP firewall rules are restricted to the QA runner before starting it.

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
