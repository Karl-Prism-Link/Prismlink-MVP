# PRISM LINK monitoring deployment

This is a test monitoring setup for Pipecat, Aurora, Prometheus, Node Exporter, and Grafana. It combines the existing metrics endpoint, alert rules, and voice operations dashboard.

## Signals collected

- Pipecat API request counts, response codes and latency.
- Live call counts, completed calls, fallback outcomes, total call duration, and voice turn latency.
- Database operation latency and errors.
- Aurora SIP registration, calls, RTP flow, callback outcomes, and control API health when the Aurora exporter is installed.
- Host CPU, memory, disk, and network through Node Exporter.
- Target reachability through Prometheus `up`.

The Pipecat `/metrics` endpoint was verified responding during setup. Runtime metrics show the version currently running on the server; compare them with the repository after deployment.

## Configure Prometheus

Use `prometheus/prometheus.hpmicro.yml` as the complete scrape configuration. Before starting Prometheus, replace `replace-with-aurora-tailscale-ip` with the private address of the Aurora metrics host. Keep that value in the server's local configuration and do not commit private addresses.

Install the file as the Prometheus config and install `prometheus/alerts/prismlink.yml` alongside it under an `alerts/` directory. Preserve the relative layout so the config's `rule_files` entry resolves correctly.

Validate and restart:

```bash
sudo promtool check config /etc/prometheus/prometheus.yml
sudo promtool check rules /etc/prometheus/alerts/prismlink.yml
sudo systemctl restart prometheus
sudo systemctl --no-pager --full status prometheus
```

This example assumes Prometheus runs as a host service. If it runs in Docker, container loopback is not the host's loopback; configure host networking or a restricted route to the host. Keep the Pipecat API private.

## Configure Grafana

1. Install `grafana/provisioning/datasources/prometheus.yml` in Grafana's datasource provisioning directory.
2. Install `grafana/provisioning/dashboards/provider.yml` in the dashboard provisioning directory.
3. Place `grafana/dashboards/prismlink-voice-overview.json` at the provider's configured dashboard path.
4. Restart Grafana and open **PRISM LINK | Voice Operations**.

The datasource UID `prometheus` matches the dashboard JSON. The provisioning example assumes Grafana can reach Prometheus at `http://localhost:9090`.

## Verify the setup

1. On the API host, request `/metrics` locally and confirm application metrics are present.
2. Open Prometheus **Status → Targets** and confirm the Pipecat, Aurora, Prometheus, and both Node Exporter jobs are **UP**.
3. If a target is down, check the service, exporter port, and private network route from the Prometheus host. Do not expose control or exporter ports to the public internet.
4. Make one controlled test call. Confirm counters change, RTP metrics move in both directions, and active call gauges return to zero after hangup.
5. Review fallback outcomes and voice turn latency. A zero sample count means that latency has not yet been measured by the running process.

Prometheus evaluates the alert rules, but notification delivery is not configured here. Add an Alertmanager receiver separately before relying on external notifications. Use synthetic test calls and keep caller details, transcripts, tokens, and private addresses out of metric labels and committed configuration.
