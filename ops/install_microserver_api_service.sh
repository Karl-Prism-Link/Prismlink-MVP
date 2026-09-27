#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR=/opt/pipecat/prism-link-mvp
SOURCE_FILE=${PROJECT_DIR}/ops/prism-link-api.service
SERVICE_FILE=/etc/systemd/system/prism-link-api.service

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script with sudo." >&2
  exit 1
fi

if [[ ! -x ${PROJECT_DIR}/.venv/bin/python || ! -f ${SOURCE_FILE} ]]; then
  echo "PRISM LINK runtime or service definition is missing." >&2
  exit 1
fi

runuser -u prismlink -- bash -lc "cd '${PROJECT_DIR}' && .venv/bin/python -c 'from apps.api.main import app'"
install -o root -g root -m 0644 "${SOURCE_FILE}" "${SERVICE_FILE}"
systemctl daemon-reload
systemctl stop prism-link-api.service 2>/dev/null || true

old_pids=$(pgrep -f '[u]vicorn apps.api.main:app.*--port 8000' || true)
if [[ -n ${old_pids} ]]; then
  kill -TERM ${old_pids}
  for _ in {1..20}; do
    kill -0 ${old_pids} 2>/dev/null || break
    sleep 0.25
  done
fi

systemctl enable --now prism-link-api.service
systemctl is-active --quiet prism-link-api.service
for _ in {1..20}; do
  if curl --fail --silent http://127.0.0.1:8000/health >/dev/null; then
    break
  fi
  sleep 0.5
done
curl --fail --silent --show-error http://127.0.0.1:8000/health >/dev/null
curl --fail --silent --show-error http://127.0.0.1:8000/metrics >/dev/null
echo "PRISM LINK API service is active and metrics are available."
