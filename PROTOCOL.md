# Device ↔ Gateway WebSocket Protocol (v1)

The ESP32 opens **one** secure WebSocket to the gateway, e.g.
`wss://esp-gateway.noblehaus.uk/ws`. The gateway bridges to the butler
`/api/voice/stream` brain and to Kokoro/Groq. The device never holds the butler
key — only a per-device `device_token`.

Two frame kinds are used:
- **Text frames** = JSON control/event messages (below).
- **Binary frames** = raw audio payload, **PCM signed-16-bit little-endian, mono**.
  Direction implies the stream: device→gateway binary = mic audio; gateway→device
  binary = TTS audio. No per-frame header needed.

## Handshake

Device sends first, immediately after connect:

```json
{
  "type": "hello",
  "proto": 1,
  "device_token": "<provisioned secret>",
  "user_id": "invite_butler_001",
  "capture": { "rate": 16000 },
  "playback": { "rate": 16000 }
}
```

Gateway validates `device_token`, then replies:

```json
{ "type": "ready", "session_id": "<uuid>", "playback": { "rate": 16000 } }
```

…or `{ "type": "error", "code": "unauthorized" }` and closes (code 4001).

Audio is always PCM16 mono. `capture.rate` is what the device's mic sends;
`playback.rate` is what it wants TTS at (gateway resamples Kokoro to match).
Default 16 kHz both ways (Whisper-friendly, ES8311-friendly). 16 kHz / 20 ms =
320 samples = 640 bytes per binary frame.

## Device → Gateway

| Message | Meaning |
|---|---|
| `{"type":"hello",...}` | handshake (above) |
| `{"type":"audio_start"}` | begin of utterance (push-to-talk press, or VAD onset) |
| *binary frames* | mic PCM16 mono between start and end |
| `{"type":"audio_end"}` | end of utterance → gateway runs STT on buffered audio |
| `{"type":"text","text":"..."}` | typed/dev input — skips STT, goes straight to butler |
| `{"type":"set_user","user_id":"..."}` | switch active user (on-screen picker), no reconnect |
| `{"type":"cancel"}` | barge-in: stop current TTS + generation |
| `{"type":"ping"}` | keepalive (or rely on WS ping/pong) |

In **open-mic mode** (Phase 7) the device omits start/end and streams binary
continuously; the gateway's VAD segments utterances server-side.

## Gateway → Device

| Message | Meaning |
|---|---|
| `{"type":"ready",...}` | handshake ack (above) |
| `{"type":"state","value":"listening\|thinking\|speaking\|idle"}` | drives status indicator |
| `{"type":"stt","text":"..."}` | recognized transcript (show what the user said) |
| `{"type":"say","text":"..."}` | assistant text (caption/subtitle) — accumulated from butler deltas |
| `{"type":"tts_start","id":N}` | TTS audio for utterance N follows as binary frames |
| *binary frames* | TTS PCM16 mono |
| `{"type":"tts_end","id":N}` | end of TTS audio |
| `{"type":"card","card":{...}}` | **screen control** — render a structured card (schema below) |
| `{"type":"image","format":"png","data":"<base64>"}` | **screen control** — full custom image (server-rendered SVG→PNG, see below) |
| `{"type":"users","users":[{"id","name"}]}` | optional list for the on-screen user picker |
| `{"type":"error","code":"...","message":"..."}` | error |

## Card schema (`display_on_device`)

Claude calls the butler tool `display_on_device`; the butler SSE pipeline emits a
`device_card` event; the gateway forwards it to the device as `{"type":"card","card":{...}}`.

```json
{
  "op": "card",
  "title": "Weather — London",
  "icon": "refresh",
  "accent": "#3b82f6",
  "subtitle": "Today",
  "rows": ["Cloudy with light rain"],
  "fields": [
    { "label": "Temp", "value": "18°C" },
    { "label": "Wind", "value": "12 mph" }
  ],
  "meters": [ { "label": "Humidity", "value": 0.74 } ],
  "status": { "text": "Updated", "color": "#16a34a" },
  "ttl_ms": 0
}
```

Card building blocks (combine freely under `op:"card"`):
- `title` + optional `icon` + `accent` (hex) — the heading.
- `subtitle` — dim line under the title.
- `rows[]` — free-text lines.
- `fields[]` — `{label,value}` rendered **two-column** (label left, value right).
- `meter` / `meters[]` — `{label,value}` with value 0..1 → accent-coloured progress bar(s).
- `status` — `{text,color}` → a coloured pill.

Other `op`s:
- `text` — `{"op":"text","title":"…","body":"…"}` — title + a wrapped paragraph.
- `toast` — `{"op":"toast","message":"…","ttl_ms":3000}` — transient overlay banner; auto-hides after `ttl_ms` (default 3000).
- `clear` — `{"op":"clear"}` — blank the card area.

## Image (`display_image`)

For visuals a structured card can't express (charts, diagrams, styled layouts), Claude
calls the butler tool `display_image` with a complete **SVG** (sized 352×280). The butler
SSE pipeline emits a `device_image` event; the **gateway rasterizes** the SVG to a PNG
(cairosvg, 352×280) and sends it to the device as one JSON text message:

```json
{ "type": "image", "format": "png", "data": "<base64 PNG>" }
```

Base64-in-JSON (not a binary frame) keeps the image on a text frame, cleanly separated
from the binary TTS audio stream that may be flowing concurrently. The device
base64-decodes, decodes the PNG (LVGL lodepng → PSRAM), and blits it into the card area.

Device constraints baked into the choices above (see also BUILD_LOG / claude-esp memory):
- **Width 352** (multiple of 32 px → 64-byte rows): the SH8601 QSPI panel's PSRAM-sourced
  SPI DMA needs cache-line-aligned transfers.
- **LVGL draw buffer height 40** (~29 KB/flush): the same DMA path rejects single
  transfers above ~50 KB, so flushes are kept small.
- ESP32-only: `display_image` is registered for `surface=="device"` sessions, never the
  website/PWA (which uses `display_in_chat`).

`icon` is rendered with LVGL's built-in glyphs; supported: check, ok, warning, alert,
info, bell, music, audio, media, video, home, mail, image, wifi, battery, location, gps,
settings, list, calendar, refresh, play, pause, download, upload, power, charge, phone,
call, file (unknown → no icon). `accent`/`status.color` are hex. `meter` value is 0..1.
`ttl_ms` 0 = persist (only `toast` auto-dismisses).

## Lifecycle

- Keepalive: WS ping/pong ~20 s.
- On disconnect the device reconnects with exponential backoff and replays `hello`.
- `cancel` lets the user interrupt (barge-in); gateway aborts the butler stream and
  stops sending TTS for the current turn.

## Auth boundary

```
ESP32 ──{device_token, user_id}──> gateway ──X-API-Key: INTERNAL_API_KEY,
                                              body.user_id = <chosen user>──> butler
```

The gateway maps a validated `device_token` to the set of `user_id`s that device is
allowed to act as; the device picks one. The butler's `get_internal_or_user`
(`deps.py:357`) accepts the internal key and takes `user_id` from the body — so per-user
memory works with no per-user secret on the device.
