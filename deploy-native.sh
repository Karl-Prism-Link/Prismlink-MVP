#!/bin/bash
set -e

echo "🚀 Prism Link Monitoring (Native Ubuntu Setup)"
echo ""

# Step 1: Install Grafana
echo "📦 Installing Grafana..."
if ! which grafana-server > /dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y grafana-server 2>&1 | grep -E "Setting up|already" || true
fi
echo "✓ Grafana installed"
echo ""

# Step 2: Configure Prometheus
echo "📝 Configuring Prometheus..."
sudo tee /var/snap/prometheus/current/prometheus.yml > /dev/null << 'PROM'
global:
  scrape_interval: 30s
  evaluation_interval: 30s
  external_labels:
    monitor: 'prism-link-pilot'

rule_files:
  - '/var/snap/prometheus/current/prometheus-alerts.yml'

alerting:
  alertmanagers:
    - static_configs:
        - targets: []

scrape_configs:
  - job_name: 'prometheus'
    scrape_interval: 15s
    static_configs:
      - targets: ['localhost:9090']

  - job_name: 'prism-link-api'
    scrape_interval: 30s
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics'
PROM
echo "✓ Prometheus config updated"
echo ""

# Step 3: Add alert rules
echo "📋 Adding alert rules..."
sudo tee /var/snap/prometheus/current/prometheus-alerts.yml > /dev/null << 'ALERTS'
groups:
  - name: prism_link_alerts
    interval: 30s
    rules:
      - alert: CallFailureRate
        expr: |
          (rate(calls_completed_total[5m]) / rate(calls_started_total[5m])) < 0.9
          or
          increase(calls_failed_total[5m]) > 0
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Call failure rate above threshold"
          description: "Check API logs and Deepgram/Groq connectivity."

      - alert: PipelineLatencyHigh
        expr: |
          histogram_quantile(0.95, rate(stg_latency_seconds_bucket[5m])) > 2.5
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "Pipeline response time exceeds 2.5s"
          description: "Check which stage is slow (STT, Router, LLM, TTS)."

      - alert: DatabaseError
        expr: |
          increase(db_errors_total[5m]) > 0
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Database errors detected"
          description: "Check SQLite connectivity and disk space."
ALERTS
echo "✓ Alert rules configured"
echo ""

# Step 4: Restart Prometheus
echo "🔄 Restarting Prometheus..."
sudo snap restart prometheus
sleep 3
echo "✓ Prometheus restarted"
echo ""

# Step 5: Enable and start Grafana
echo "🚀 Starting Grafana..."
sudo systemctl enable grafana-server
sudo systemctl restart grafana-server
sleep 3
echo "✓ Grafana started"
echo ""

# Step 6: Verify
echo "✅ Deployment complete!"
echo ""
echo "Access points:"
echo "  Prometheus: http://192.168.x.x:9090"
echo "  Grafana:    http://192.168.x.x:3000 (admin/admin)"
echo ""
echo "Next: Import dashboard JSON to Grafana"
