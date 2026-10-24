# QA control MCP setup

`qa_mcp_server.py` is a small stdio MCP server for operating the existing one-shot QA unit. It exposes only:

- `qa_status`: unit active state and latest saved T001 result.
- `qa_run_t001`: starts `prism-link-voice-qa.service` and returns the resulting JSON.

Neither tool accepts arguments. The server has no general shell tool, target override, arbitrary path, or environment input. T001 remains pinned to `sip:02825523645@100.105.4.49:5060`.

## Install on hpmicro

Install the repository at `/opt/prism-link-voice-qa` and install the runner's dependencies as described in `README.md`. Then install the narrowly scoped sudoers rule:

```bash
sudo install -o root -g root -m 0440 \
  qa/voice-testing/prism-link-voice-qa-mcp.sudoers \
  /etc/sudoers.d/prism-link-voice-qa-mcp
sudo visudo -cf /etc/sudoers.d/prism-link-voice-qa-mcp
```

Run the MCP process as `prismlink`, not root. Example stdio client configuration:

```json
{
  "mcpServers": {
    "prism-link-voice-qa": {
      "command": "/usr/bin/python3",
      "args": ["/opt/prism-link-voice-qa/qa/voice-testing/qa_mcp_server.py"]
    }
  }
}
```

The MCP client must be on hpmicro or have an approved secure stdio bridge to it. This script is not a network listener and does not change Cloudflare, SSH, Tailscale, or firewall configuration. Before invoking `qa_run_t001`, confirm the API is using the isolated QA data and Aurora SIP/RTP access is restricted to the runner's Tailscale address as described in `README.md`.

## Local protocol check

```bash
python3 -m unittest discover -s tests -p 'test_voice_qa_mcp_server.py' -v
```
