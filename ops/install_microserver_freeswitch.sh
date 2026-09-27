#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script with sudo." >&2
  exit 1
fi

PROJECT_DIR=/opt/pipecat/prism-link-mvp
FS_PREFIX=/usr/local/freeswitch
FS_CONF_DIR=${FS_PREFIX}/conf
FS_MODULE_DIR=${FS_PREFIX}/mod
GATEWAY_FILE=${FS_CONF_DIR}/sip_profiles/external/2talk.xml
MODULES_FILE=${FS_CONF_DIR}/autoload_configs/modules.conf.xml
DIALPLAN_FILE=${FS_CONF_DIR}/dialplan/public/00_prism_link.xml
SERVICE_FILE=/etc/systemd/system/prism-link-voice.service
MODULE_URL=https://github.com/amigniter/mod_audio_stream/releases/download/v1.0.3/mod-audio-stream_1.0.3_amd64.deb
MODULE_SHA256=769741066614dfb5ccd0fa5f5e6a507da229c3e6579f15d66f1cc0ee6fdfd04c
BACKUP_DIR=/var/backups/prism-link-freeswitch/$(date -u +%Y%m%dT%H%M%SZ)

for path in "${PROJECT_DIR}" "${MODULES_FILE}" "${GATEWAY_FILE}"; do
  if [[ ! -e ${path} ]]; then
    echo "Required path is missing: ${path}" >&2
    exit 1
  fi
done

if ! id prismlink >/dev/null 2>&1; then
  echo "The prismlink service account does not exist." >&2
  exit 1
fi

DID=$(sed -n 's/.*name="extension" value="\([^"]*\)".*/\1/p' "${GATEWAY_FILE}" | head -n 1)
if [[ -z ${DID} ]]; then
  DID=$(sed -n 's/.*name="username" value="\([^"]*\)".*/\1/p' "${GATEWAY_FILE}" | head -n 1)
fi
if [[ ! ${DID} =~ ^[0-9]+$ ]]; then
  echo "Could not derive a numeric inbound DID from ${GATEWAY_FILE}." >&2
  exit 1
fi

install -d -m 0700 "${BACKUP_DIR}"
cp -a "${MODULES_FILE}" "${BACKUP_DIR}/modules.conf.xml"
[[ -e ${DIALPLAN_FILE} ]] && cp -a "${DIALPLAN_FILE}" "${BACKUP_DIR}/00_prism_link.xml"
[[ -e ${SERVICE_FILE} ]] && cp -a "${SERVICE_FILE}" "${BACKUP_DIR}/prism-link-voice.service"

TMP_DIR=$(mktemp -d /tmp/prism-mod-audio.XXXXXX)
trap 'rm -rf -- "${TMP_DIR}"' EXIT
PACKAGE=${TMP_DIR}/mod-audio-stream_1.0.3_amd64.deb
curl --fail --location --silent --show-error --output "${PACKAGE}" "${MODULE_URL}"
echo "${MODULE_SHA256}  ${PACKAGE}" | sha256sum --check --status
dpkg-deb --extract "${PACKAGE}" "${TMP_DIR}/extract"
MODULE_SOURCE=${TMP_DIR}/extract/usr/lib/freeswitch/mod/mod_audio_stream.so
if [[ ! -s ${MODULE_SOURCE} ]]; then
  echo "The downloaded package did not contain mod_audio_stream.so." >&2
  exit 1
fi

if LD_LIBRARY_PATH=${FS_PREFIX}/lib ldd "${MODULE_SOURCE}" | grep -q 'not found'; then
  LD_LIBRARY_PATH=${FS_PREFIX}/lib ldd "${MODULE_SOURCE}" >&2
  echo "The module has unresolved shared-library dependencies." >&2
  exit 1
fi

install -o root -g root -m 0755 "${MODULE_SOURCE}" "${FS_MODULE_DIR}/mod_audio_stream.so"

if ! grep -q 'load module="mod_audio_stream"' "${MODULES_FILE}"; then
  sed -i '/<!-- Third party modules -->/a\    <load module="mod_audio_stream"/>' "${MODULES_FILE}"
fi

cat >"${SERVICE_FILE}" <<'EOF'
[Unit]
Description=PRISM LINK FreeSWITCH voice runtime
Wants=network-online.target
After=network-online.target freeswitch.service
Requires=freeswitch.service

[Service]
Type=simple
User=prismlink
Group=prismlink
WorkingDirectory=/opt/pipecat/prism-link-mvp
Environment=PYTHONUNBUFFERED=1
Environment=FS_WS_HOST=127.0.0.1
Environment=FS_WS_PORT=8765
Environment=FS_SAMPLE_RATE=16000
Environment=FS_MAX_CALLS=10
ExecStart=/opt/pipecat/prism-link-mvp/.venv/bin/python -m apps.voice.bot_freeswitch
Restart=on-failure
RestartSec=3s
TimeoutStopSec=20s
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/opt/pipecat/prism-link-mvp
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
LimitNOFILE=8192

[Install]
WantedBy=multi-user.target
EOF

cat >"${DIALPLAN_FILE}" <<EOF
<include>
  <extension name="prism_link_inbound">
    <condition field="destination_number" expression="^(${DID})$">
      <action application="set" data="STREAM_PLAYBACK=true"/>
      <action application="set" data="STREAM_SAMPLE_RATE=16000"/>
      <action application="set" data="STREAM_BUFFER_SIZE=20"/>
      <action application="set" data="api_on_answer=uuid_audio_stream \${uuid} start ws://127.0.0.1:8765/calls/\${uuid}?called_number=\${destination_number}&amp;caller_number=\${caller_id_number} mono 16k"/>
      <action application="answer"/>
      <action application="park"/>
    </condition>
  </extension>
</include>
EOF

chown -R prismlink:prismlink "${PROJECT_DIR}"
chmod 0600 "${PROJECT_DIR}/.env"
[[ -d ${PROJECT_DIR}/.secrets ]] && chmod 0700 "${PROJECT_DIR}/.secrets"

systemctl daemon-reload
systemctl enable --now prism-link-voice.service

EVENT_SOCKET_FILE=${FS_CONF_DIR}/autoload_configs/event_socket.conf.xml
FS_PASSWORD=$(sed -n 's/.*name="password" value="\([^"]*\)".*/\1/p' "${EVENT_SOCKET_FILE}" | head -n 1)
if [[ -z ${FS_PASSWORD} ]]; then
  echo "Could not read the local FreeSWITCH event-socket password." >&2
  exit 1
fi

FS_CLI=(/usr/local/bin/fs_cli -H 127.0.0.1 -P 8021 -p "${FS_PASSWORD}")
if ! "${FS_CLI[@]}" -x 'module_exists mod_audio_stream' | grep -q true; then
  "${FS_CLI[@]}" -x 'load mod_audio_stream'
fi
"${FS_CLI[@]}" -x 'reloadxml'

systemctl is-active --quiet prism-link-voice.service
ss -lnt | grep -q '127.0.0.1:8765'
"${FS_CLI[@]}" -x 'module_exists mod_audio_stream' | grep -q true

echo "PRISM LINK listener is active on 127.0.0.1:8765."
echo "mod_audio_stream is loaded and the matching 2talk DID route is active."
echo "Backups: ${BACKUP_DIR}"
