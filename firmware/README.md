# claude-esp firmware

ESP-IDF app for the Waveshare ESP32-S3-Touch-AMOLED-1.8. Connects to the
`esp-gateway` over a single WebSocket, streams mic audio (push-to-talk), plays
back TTS, and renders cards Claude pushes via `display_on_device`.

Bootstrapped from the same Waveshare BSP as `pet-esp` (display + FT3168 touch +
ES8311 codec). Targets **ESP-IDF 5.3.x** and **LVGL 9**.

## Modules (`main/`)
- `main.c` — init order: NVS → display/LVGL → UI → audio → WiFi → WebSocket.
- `app_config.[ch]` — config in NVS (`wifi_ssid/pass`, `gateway_uri`, `device_token`, `user_id`) with compile-time defaults.
- `net.[ch]` — WiFi STA + auto-reconnect.
- `ws_client.[ch]` — `esp_websocket_client` (WSS via the cert bundle); hello handshake; dispatches `state`/`card`/`error`; routes binary frames to audio; sends `audio_start/end`, mic PCM, `text`, `set_user`, `cancel`.
- `audio.[ch]` — ES8311 mic capture (I2S RX task) + speaker playback (stream buffer → I2S TX). PCM16 mono @ 16 kHz.
- `ui.[ch]` — LVGL: status line, card panel (title/rows/meter/accent), hold-to-talk button.
- `protocol.h` — message-type strings shared with the gateway (see ../PROTOCOL.md).

## Build & flash
```bash
. ~/.espressif/v5.3.5/esp-idf/export.sh
cd firmware
idf.py set-target esp32s3          # first time (downloads the BSP component)
idf.py build
idf.py -p /dev/cu.usbmodem* flash monitor
```

## Configure (before flashing)
Set first-boot defaults at build time, e.g.:
```bash
idf.py build \
  -DCFG_WIFI_SSID='"YourSSID"' -DCFG_WIFI_PASS='"YourPass"' \
  -DCFG_GATEWAY_URI='"wss://esp-gateway.noblehaus.uk/ws"' \
  -DCFG_DEVICE_TOKEN='"your-device-token"' \
  -DCFG_USER_ID='"invite_butler_001"'
```
…or flash with the defaults and set them later via NVS / the (planned) on-device
settings UI. For LAN dev, use `ws://192.168.1.117:8770/ws` (no TLS).

## Status
Compiles against the BSP. **Not yet flashed / runtime-verified** — audio I/O (ES8311
duplex) and the touch push-to-talk need on-hardware testing. See ../BUILD_LOG.md.
