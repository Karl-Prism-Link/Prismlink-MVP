# Remote voice QA MCP

This is a Cloudflare Access protected, stateless Streamable HTTP MCP endpoint for the fixed T001 test. It exposes only `qa_status` and `qa_run_t001`; neither tool accepts arguments. The run tool starts only `prism-link-voice-qa.service`, which calls only `sip:02825523645@100.105.4.49:5060` over direct SIP to Aurora.

## Cloudflare route

The dedicated endpoint is `https://voice-qa-mcp.prismlink.tech/mcp`. Cloudflare Tunnel routes that hostname to `http://127.0.0.1:8765` on hpmicro. Cloudflare Access Managed OAuth protects it, with an allow policy for `karl@prismlink.tech`. The MCP service validates the Access JWT signature, issuer, audience, expiry, and email claim. The existing SSH and MCP Portal hostnames are separate and unchanged.

The Cloudflare hostname is prepared, but calls will fail until the hpmicro MCP service is installed and running. Do not share or connect the endpoint until the local listener is verified.

## Install on hpmicro

First complete the runner setup in `README.md`: install the repository at `/opt/prism-link-voice-qa`, install Linphone and runner dependencies, prepare the private Deepgram env file and results directory, and verify the API uses isolated QA data and Aurora SIP/RTP is restricted to the runner's Tailscale source.

Install the JWT dependency in the QA virtual environment:

```bash
sudo -u prismlink /var/lib/prism-link-voice-qa/venv/bin/pip install \
  -r /opt/prism-link-voice-qa/qa/voice-testing/requirements-mcp.txt
```

Create `/etc/prism-link-voice-qa-mcp.env` as `root:prismlink`, mode `0640`, with:

```ini
CF_ACCESS_TEAM_DOMAIN=red-shape-da7b.cloudflareaccess.com
CF_ACCESS_AUD=fc2ced841c4b8f71ebe11d8b1a61339f9cb9641df601f59515d748cf3ddd5848
CF_ACCESS_ALLOWED_EMAIL=karl@prismlink.tech
```

Install the narrowly scoped sudoers file. It permits the `prismlink` account to read the fixed QA unit state and start that unit only:

```bash
sudo install -o root -g root -m 0440 \
  /opt/prism-link-voice-qa/qa/voice-testing/prism-link-voice-qa-mcp.sudoers \
  /etc/sudoers.d/prism-link-voice-qa-mcp
sudo visudo -cf /etc/sudoers.d/prism-link-voice-qa-mcp
```

Install and start the loopback-only MCP service:

```bash
sudo install -o root -g root -m 0644 \
  /opt/prism-link-voice-qa/qa/voice-testing/prism-link-voice-qa-mcp.service \
  /etc/systemd/system/prism-link-voice-qa-mcp.service
sudo systemctl daemon-reload
sudo systemctl enable --now prism-link-voice-qa-mcp.service
sudo systemctl status prism-link-voice-qa-mcp.service --no-pager
ss -lntp | grep ':8765'
```

The listener must bind only to `127.0.0.1:8765`; Cloudflare Tunnel is the only network path to it. Keep the Access environment file and service code root-owned. The service runs as `prismlink` and has no general shell tool.

## Connect an MCP client

Configure the remote MCP URL `https://voice-qa-mcp.prismlink.tech/mcp` using an MCP client that supports Streamable HTTP and Cloudflare Access OAuth. Sign in as `karl@prismlink.tech`. Confirm the client lists exactly `qa_status` and `qa_run_t001`, then call `qa_status` before running T001.

The server does not expose a health page or accept a caller-supplied SIP URI, shell command, environment variable, or filesystem path. Test the endpoint only after the service is active and the QA isolation checks in `README.md` pass.

## Local verification

```bash
python3 -m unittest discover -s tests -p 'test_voice_qa_mcp_server.py' -v
python3 -m unittest discover -s tests -p 'test_voice_qa_mcp_http.py' -v
```
