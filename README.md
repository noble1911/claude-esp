# claude-esp

An internet-connected ESP32 voice device (Waveshare ESP32-S3-Touch-AMOLED-1.8) that
talks to the home server's **butler** brain, lets you converse with Claude as a
**selectable user** (per-user memory), and lets Claude **draw cards on the screen**.

See **[PLAN.md](PLAN.md)** for the architecture and **[PROTOCOL.md](PROTOCOL.md)** for
the device↔gateway WebSocket contract. **[BUILD_LOG.md](BUILD_LOG.md)** tracks progress.

## Layout
- `gateway/` — the bridge service (Python): device WS ↔ Groq STT ↔ butler `/api/voice/stream` ↔ Kokoro TTS, plus screen-card forwarding.
- `firmware/` — the ESP32 app (ESP-IDF), bootstrapped from the pet-esp Waveshare BSP. *(WIP)*

## Gateway quick start
```bash
cd gateway
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# config via env (see esp_gateway/config.py)
python -m esp_gateway          # starts the WS server on :8770
python -m esp_gateway.devclient --text "what's the weather"   # stand-in client, no hardware
```
