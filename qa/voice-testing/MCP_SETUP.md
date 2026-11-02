# Voice QA host setup (hpmicro)

This guide installs the fixed T001 runner and its optional MCP control service on hpmicro. It is written for an Ubuntu 22.04 x86_64 host. Commands are copy/pasteable; run them from an SSH/Termalin shell on **hpmicro**.

T001 is pinned in code and in systemd to:

```text
sip:02825523645@100.105.4.49:5060
```

This is a direct SIP call to Aurora over Tailscale. No 2talk trunk or PSTN route is used. Never change the target URI.

## Safety gates — check before enabling anything

T001 resolves the called number against the Prism Link API's tenant data. It must be a dedicated test tenant in a dedicated QA data store; do not run this against a customer tenant or the live API.

The checks already performed for this setup found:

- hpmicro's API is configured to use the shared PostgreSQL database `prism_link`.
- The matching `02825523645` record is Luxe Hair & Beauty with status `trial`, but the tenant table has no test/isolation flag. A trial status alone does not prove the API is using isolated QA data.
- Aurora's UFW is inactive. Its SIP and RTP sockets are therefore not currently protected by the required host firewall policy.

**Until both items are resolved, do not start the runner or MCP service, and do not call T001.** In particular, do not enable the MCP endpoint: its `qa_run_t001` tool can start the call. The installation commands below only install dependencies and unit/config files; they leave both services stopped.

Before enabling either service, an owner must confirm that the API is pointed at a dedicated QA database/tenant fixture and verify that Aurora's host firewall and Tailscale ACLs restrict UDP SIP 5060 and the configured RTP range (currently UDP 26116–26130) to hpmicro's Tailscale address `100.92.110.101`. If the policy or RTP range differs, stop and resolve it with the server owner first. Do not change firewall rules as part of this guide.

## 1. Preflight

```bash
hostname
. /etc/os-release && printf '%s %s\n' "$PRETTY_NAME" "$(uname -m)"
df -h /
git -C /opt/prism-link-voice-qa status --short --branch
git -C /opt/prism-link-voice-qa rev-parse --abbrev-ref HEAD
systemctl is-active prism-link-api.service
curl --fail --silent --show-error http://100.92.110.101:8000/health/ready
printf '\nAurora Tailscale reachability (no call):\n'
tailscale ping --c 1 100.105.4.49
```

Expected: Ubuntu 22.04, `x86_64`, at least 2 GB free, branch `codex/automated-voice-qa`, API readiness `ready`, and Aurora reachable over Tailscale. If the checkout has local changes, stop before updating it.

Inspect the exact tenant row without selecting other tenant data:

```bash
sudo -u postgres psql -X -d prism_link -c \
  "SELECT name, phone_number, status FROM public.tenants WHERE phone_number = '02825523645';"
```

A matching row is **not** proof of isolation. Confirm separately that the API service used for QA has a dedicated QA database and cannot resolve production tenants. If that cannot be demonstrated, stop here.

Check Aurora's firewall and sockets from a shell on Aurora:

```bash
sudo ufw status verbose
sudo ss -lunp | grep -E ':(5060|2611[6-9]|2612[0-9]|26130)\b'
```

The firewall must be active and its effective rules must allow only hpmicro's Tailscale source to the SIP and RTP ports. If UFW is inactive, rules are unclear, or the RTP range is not confirmed, stop; do not enable/change the firewall or place a call under this guide.

## 2. Install host packages and checkout

The packages below provide audio synthesis/conversion only. This does not upgrade the server or restart its application services.

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends espeak-ng ffmpeg git curl ca-certificates
```

The repository must be a clean checkout of the named branch:

```bash
sudo git -C /opt/prism-link-voice-qa fetch origin codex/automated-voice-qa
sudo git -C /opt/prism-link-voice-qa switch codex/automated-voice-qa
sudo git -C /opt/prism-link-voice-qa pull --ff-only origin codex/automated-voice-qa
git -C /opt/prism-link-voice-qa status --short --branch
```

Expected branch: `codex/automated-voice-qa`, with no modified files.

## 3. Install the isolated Python environment and Linphone

Linphone publishes its Python SDK wheel for Linux x86_64. This installs the pinned 5.5.38 wheel for Python 3.12.

```bash
sudo install -d -o prismlink -g prismlink -m 0700 \
  /var/lib/prism-link-voice-qa \
  /var/lib/prism-link-voice-qa/results \
  /var/lib/prism-link-voice-qa/config \
  /var/lib/prism-link-voice-qa/data \
  /var/lib/prism-link-voice-qa/cache
sudo -u prismlink python3.12 -m venv /var/lib/prism-link-voice-qa/venv
sudo -u prismlink /var/lib/prism-link-voice-qa/venv/bin/pip install \
  -r /opt/prism-link-voice-qa/qa/voice-testing/requirements-mcp.txt
sudo -u prismlink curl --fail --location --output /tmp/linphone-5.5.38-cp312-cp312-linux_x86_64.whl \
  https://download.linphone.org/releases/linphone-python/5.5/linux-x86_64/linphone-5.5.38-cp312-cp312-linux_x86_64.whl
sudo -u prismlink /var/lib/prism-link-voice-qa/venv/bin/pip install \
  /tmp/linphone-5.5.38-cp312-cp312-linux_x86_64.whl
sudo rm -f /tmp/linphone-5.5.38-cp312-cp312-linux_x86_64.whl
sudo -u prismlink /var/lib/prism-link-voice-qa/venv/bin/python -c \
  'from linphone import Factory; print("Linphone SDK", Factory.get().get_version())'
espeak-ng --version
ffmpeg -version | head -n 1
```

On a freshly provisioned host, create the private runner environment file and enter the Deepgram key locally (never paste it into chat or commit it):

```bash
sudo install -o root -g prismlink -m 0640 /dev/null /etc/prism-link-voice-qa.env
sudoedit /etc/prism-link-voice-qa.env
sudo chown root:prismlink /etc/prism-link-voice-qa.env
sudo chmod 0640 /etc/prism-link-voice-qa.env
```

Its only setting is `DEEPGRAM_API_KEY=...`. The API key is used to transcribe test audio; do not print or source the file in an interactive shell.

## 4. Install the one-shot runner (leave stopped)

```bash
sudo install -o root -g root -m 0644 \
  /opt/prism-link-voice-qa/qa/voice-testing/prism-link-voice-qa.service \
  /etc/systemd/system/prism-link-voice-qa.service
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/prism-link-voice-qa.service
sudo systemctl show prism-link-voice-qa.service --property=LoadState,ActiveState,UnitFileState
```

Check the target without making a call:

```bash
sudo -u prismlink /var/lib/prism-link-voice-qa/venv/bin/python \
  /opt/prism-link-voice-qa/qa/voice-testing/run_t001.py --dry-run
```

Expected output includes `"status": "READY"` and the fixed URI above. This is a dry run only.

## 5. Install MCP controls (leave stopped)

Cloudflare Access already protects `https://voice-qa-mcp.prismlink.tech/mcp` and routes it to hpmicro's loopback listener. The service exposes only `qa_status` and `qa_run_t001`. Do not start it until both safety gates above have been positively cleared.

Create its private Access configuration:

```bash
sudo install -o root -g prismlink -m 0640 /dev/null /etc/prism-link-voice-qa-mcp.env
sudoedit /etc/prism-link-voice-qa-mcp.env
```

Enter these values in the editor (the audience is the Cloudflare Access application audience):

```ini
CF_ACCESS_TEAM_DOMAIN=red-shape-da7b.cloudflareaccess.com
CF_ACCESS_AUD=fc2ced841c4b8f71ebe11d8b1a61339f9cb9641df601f59515d748cf3ddd5848
CF_ACCESS_ALLOWED_EMAIL=karl@prismlink.tech
```

Then install and validate the narrow sudo permission and MCP unit. This grants `prismlink` only permission to read the fixed QA unit state and start that unit:

```bash
sudo install -o root -g root -m 0440 \
  /opt/prism-link-voice-qa/qa/voice-testing/prism-link-voice-qa-mcp.sudoers \
  /etc/sudoers.d/prism-link-voice-qa-mcp
sudo visudo -cf /etc/sudoers.d/prism-link-voice-qa-mcp
sudo install -o root -g root -m 0644 \
  /opt/prism-link-voice-qa/qa/voice-testing/prism-link-voice-qa-mcp.service \
  /etc/systemd/system/prism-link-voice-qa-mcp.service
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/prism-link-voice-qa-mcp.service
sudo systemctl show prism-link-voice-qa-mcp.service --property=LoadState,ActiveState,UnitFileState
```

Do not run `systemctl enable` or `start` while isolation is unconfirmed.

## 6. Verification (only after both safety gates are cleared)

After the QA database and firewall/ACL checks are confirmed, an owner may start the runner manually for one T001 call:

```bash
sudo systemctl start prism-link-voice-qa.service
sudo systemctl status prism-link-voice-qa.service --no-pager
sudo journalctl -u prism-link-voice-qa.service -n 80 --no-pager
sudo find /var/lib/prism-link-voice-qa/results -maxdepth 1 -type f -printf '%f\n'
```

Starting the MCP service enables remote access to `qa_run_t001`; do this only after the same checks pass:

```bash
sudo systemctl start prism-link-voice-qa-mcp.service
sudo systemctl status prism-link-voice-qa-mcp.service --no-pager
ss -lntp '( sport = :8765 )'
curl --include --max-time 5 http://127.0.0.1:8765/mcp
```

The listener must show only `127.0.0.1:8765`; an unauthenticated local request should be rejected (401/403). Then connect an MCP client to `https://voice-qa-mcp.prismlink.tech/mcp` through Cloudflare Access as `karl@prismlink.tech`, confirm it lists exactly `qa_status` and `qa_run_t001`, and call `qa_status` first. Do not invoke `qa_run_t001` until the isolated-data and firewall checks have been reconfirmed.

## Official Linphone references

- [Linphone Python installation guide](https://wiki.linphone.org/xwiki/wiki/public/view/Lib/Getting%20started/Python/)
- [Linphone 5.5 Linux x86_64 Python wheels](https://download.linphone.org/releases/linphone-python/5.5/linux-x86_64/)

## Local MCP tests

Run from the repository root:

```bash
python3 -m unittest discover -s tests -p 'test_voice_qa_mcp_server.py' -v
python3 -m unittest discover -s tests -p 'test_voice_qa_mcp_http.py' -v
```

A passing unit test or dry run does not prove an end-to-end SIP call. Report a call as passing only when the runner actually completes it and its result is reviewed.
