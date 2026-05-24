#!/usr/bin/env bash
# Flash the claude-esp firmware with your config. WiFi creds are supplied via env
# (never hard-coded) and written to a git-ignored main/secrets.h.
#
#   WIFI_SSID='MyNetwork' WIFI_PASS='mypassword' bash flash.sh
#
# Optional overrides (sensible defaults shown):
#   PORT=/dev/cu.usbmodemXXXX           # auto-detected if omitted
#   GW=ws://192.168.1.117:8770/ws       # LAN gateway (use wss://esp-gateway.<domain>/ws when tunnelled)
#   USER_ID=invite_xxxxxxxx             # butler user to converse as
#   TOKEN=changeme-device-token
set -euo pipefail
: "${WIFI_SSID:?set WIFI_SSID}"
: "${WIFI_PASS:?set WIFI_PASS}"

HERE="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-$(ls /dev/cu.usbmodem* 2>/dev/null | head -1 || true)}"
GW="${GW:-ws://192.168.1.117:8770/ws}"
USER_ID="${USER_ID:-invite_xxxxxxxx}"
TOKEN="${TOKEN:-changeme-device-token}"
[ -n "${PORT:-}" ] || { echo "No serial port found. Plug in the board and/or set PORT=/dev/cu.usbmodemXXXX"; exit 1; }

cat > "$HERE/main/secrets.h" <<EOF
#pragma once
#define CFG_WIFI_SSID    "$WIFI_SSID"
#define CFG_WIFI_PASS    "$WIFI_PASS"
#define CFG_GATEWAY_URI  "$GW"
#define CFG_USER_ID      "$USER_ID"
#define CFG_DEVICE_TOKEN "$TOKEN"
EOF
echo "wrote main/secrets.h (ssid=$WIFI_SSID gw=$GW user=$USER_ID port=$PORT)"

. "$HOME/.espressif/v5.3.5/esp-idf/export.sh" >/dev/null
cd "$HERE"
idf.py build
idf.py -p "$PORT" flash monitor
