# HomeServer patch: `display_on_device` tool

Adds a butler tool so Claude can draw structured cards on the ESP32 screen. Prepared as
a **local, un-pushed, un-deployed** branch for your review.

- **Repo:** `~/random/HomeServer` (and live on the Mac Mini at `~/home-server`)
- **Branch:** `feat/esp-display-on-device` (commit `3e5b973`)
- **Portable patch:** `docs/homeserver-display_on_device.patch` (in this repo)

## What it changes (19 insertions, 1 new file)
- `butler/tools/display_on_device.py` — new tool. `execute()` is a no-op (like `display_in_chat`).
- `butler/api/llm.py` — in the streaming voice pipeline, intercept the tool call and emit `{"type":"device_card","card":{...}}`; also added to `ROUTING_CORE_TOOLS` so Claude can use it without a `request_tools` round-trip.
- `butler/api/deps.py` — added to `ALWAYS_ALLOWED_TOOLS` and instantiated in `init_resources`.
- `butler/api/routes/voice.py` — registered on both `/api/voice/process` and `/api/voice/stream`.
- `butler/api/context.py` — one line in the voice system prompt telling Claude when to use it.
- `butler/tools/__init__.py` — export.

## How to deploy (when you're ready)
```bash
cd ~/home-server            # on the Mac Mini
git fetch && git checkout feat/esp-display-on-device   # or: git apply <patch>
docker compose -f butler/docker-compose.yml up -d --build butler-api
```
The card schema the tool emits is in `claude-esp/PROTOCOL.md`. The gateway already parses
`device_card` events and forwards them to the device (`gateway/esp_gateway/butler.py`).

## Caveat to review
`/api/voice/stream` uses `channel="voice"` for both the PWA's LiveKit voice and the ESP32.
So `display_on_device` is offered to PWA voice too; the LiveKit agent simply ignores
`device_card` events (it only forwards `visual_content`), so there's no harm — but Claude
*could* call it for a PWA-voice user where nothing renders. If that bothers you, add a
`surface`/`channel` field to `VoiceProcessRequest` and gate registration on it. Left out
to keep the diff minimal and reviewable.
