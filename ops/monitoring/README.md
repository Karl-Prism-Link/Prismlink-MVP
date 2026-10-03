# PRISM LINK voice monitoring

This configuration covers the split test deployment: Pipecat/API and Prometheus/Grafana on hpmicro; Aurora on Azure, reached over Tailscale.

## Instrumentation
- FastAPI `/metrics`: HTTP request count/latency by static route template and status.
- Pipecat live sessions: starts, active count, completed/fallback outcomes and duration.
- Voice turn latency: caller end-of-turn to first Pipecat response audio, using the existing `UserBotLatencyObserver`.
- Aurora exporter: SIP registration, active/completed calls, RTP packet/byte flow in both directions, no-flow events, callback outcomes, and event queue depth.
- Node Exporter: CPU, memory, disk and network on both hosts.

Metric labels are bounded. Never label metrics with call IDs, caller numbers, tenant names, SIP URIs, IPs, transcripts, tokens, or exception text. Keep logs free of secrets and full transcripts.

## Install on hpmicro
1. Confirm the existing Prometheus and Grafana services. Preserve their other settings.
2. Merge `prometheus/scrape-config.yml` jobs under the existing `scrape_configs:`; add `prometheus/alerts/prismlink.yml` to `rule_files:`.
3. This target config assumes Prometheus runs as a host service: Pipecat listens on `127.0.0.1:8000`. If Prometheus runs in Docker, use host networking or a restricted local proxy; do not expose Pipecat publicly.
4. Install Node Exporter on hpmicro and Aurora. Bind to loopback/Tailscale or firewall TCP 9100 so only Prometheus can reach it. Allow Aurora exporter port 9818 only from hpmicro's Tailscale IP.
5. Provision the datasource and dashboard files under Grafana's configured provisioning directories (or import the dashboard JSON). Datasource URL assumes Prometheus at `localhost:9090`.
6. Store Grafana admin credentials using the host's secret mechanism. Configure Alertmanager notification receivers separately before relying on notifications.

## Verify
- On hpmicro: `curl -fsS http://127.0.0.1:8000/metrics` should expose call, HTTP and voice-turn metrics.
- On Aurora: `curl -fsS http://127.0.0.1:8088/metrics`; keep port 8088 loopback-only.
- From hpmicro: `curl -fsS http://100.105.4.49:9818/metrics` should return sanitized Aurora metrics.
- Make a controlled PCMA/PCMU call. Confirm registration, both RTP directions, callback outcome, Pipecat active session, latency and cleanup.

Exercise no-answer/CANCEL, BYE, callback outage, RTP disconnect, SIP registration loss/recovery and provider timeout/recovery before normal use. Confirm failures are visible without retaining caller content in metrics.
