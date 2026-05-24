# Butler ESP32 Voice Device — Plan

A handheld ESP32 device (Waveshare ESP32-S3-Touch-AMOLED-1.8) that connects over
the internet to the home server, lets you converse with Claude **as a chosen user**
(reusing that user's per-user memory), and lets Claude **draw cards on the screen**.

## The four repos involved

| Repo | Path | Role here |
|------|------|-----------|
| **claude-esp** | `/Users/ron/random/claude-esp` | This project. Plan + the new **gateway** service. |
| **pet-esp** | `/Users/ron/IdeaProjects/pet-esp` | Existing ESP-IDF firmware for the *exact* board. We extend it. |
| **HomeServer** | `/Users/ron/random/HomeServer` | Butler API (the "brain" + per-user memory). Server is Mac Mini @ `192.168.1.117`, exposed via Cloudflare Tunnel. |
| **vector-llm** | `/Users/ron/random/vector-llm` | Always-on mic project. We reuse its VAD/segmentation + Kokoro resampler. |

## Architecture

```
  ESP32 (pet-esp firmware, extended)
   ├─ mic  → ES8311 → I2S RX ─┐
   ├─ speaker ← I2S TX ←──────┤   ONE secure WebSocket (wss, via Cloudflare Tunnel)
   └─ AMOLED ← LVGL cards ←───┘
                              │
                              ▼
  esp-gateway  (NEW small Python asyncio service, in claude-esp, runs on home server)
   ├─ Silero VAD + speech segmentation   ← reuse vector-llm/stt.py
   ├─ STT (Groq Whisper, like butler agent)
   ├─ calls butler  POST /api/voice/stream   with X-API-Key + chosen user_id
   │     └─ Claude + that user's memory + tools  → text deltas + display_on_device cards
   ├─ TTS: Kokoro /v1/audio/speech → resample to PCM   ← reuse vector-llm/tts.py
   └─ streams PCM audio + card events back down the same WS

  HomeServer / butler  (mostly UNCHANGED — reuses existing voice path)
   └─ new tool: display_on_device  (clone of display_in_chat → emits structured card events)
```

**Why a gateway instead of hitting butler directly:** `/api/voice/stream` takes *text*,
not audio. STT + TTS + audio transport have to live somewhere off the ESP32. The gateway
is that thin shim — and `vector-llm` already contains ~90% of its audio code.

**Why not LiveKit (the existing voice path):** Verified by reading `livekit.yaml` —
the existing voice stack is **LAN-only and does not work remotely today**. WebRTC
splits into signaling (a WS that *does* traverse the tunnel) and media (DTLS-SRTP that
does not). With `use_external_ip: false` and `turn.enabled: false`, LiveKit only
advertises the `192.168.1.117` LAN address, and nginx tunnels just `:7880` (signaling),
not the `:7881`/`:7882` media ports. So PWA voice only works on home WiFi; off-network
it fails — confirmed by the user. There is therefore nothing to reuse on the transport
side for an internet device, and a WebRTC client is also the hardest possible firmware.
A single `wss` socket traverses the tunnel cleanly for both signaling *and* audio.

**Bonus:** the gateway gives the home server its first working over-the-internet voice
path. (Separately, the PWA's remote voice could be fixed by adding a coturn TURN server
on `:443/tcp` + a tunnel route — out of scope here, but worth noting as a HomeServer bug.)

## Reuse vs. build

| Area | Status | Source |
|------|--------|--------|
| AMOLED display + FT3168 touch + LVGL | ✅ Working | pet-esp (Waveshare BSP) |
| ES8311 codec init (mic + speaker) | ⚠️ BSP funcs exist, never called | pet-esp BSP `bsp_audio_codec_*_init()` |
| Claude brain + per-user memory + tools | ✅ Working | butler `/api/voice/stream` |
| VAD + utterance segmentation | ✅ Working (server-side) | vector-llm `stt.py:37-117` |
| Kokoro TTS + WAV→PCM resample | ✅ Working | vector-llm `tts.py` |
| "Claude draws on screen" mechanism | ✅ Pattern exists | butler `display_in_chat.py` + SSE intercept |
| ESP32 audio I/O pump (I2S RX/TX) | ❌ Build | firmware |
| ESP32 WiFi STA + WSS client + NVS config | ❌ Build (`radio.c` is an ESP-NOW stub) | firmware |
| esp-gateway service | ❌ Build (~150–250 lines) | claude-esp |
| `display_on_device` card tool + card SSE event | ❌ Build (small) | butler |
| On-device LVGL card renderer | ❌ Build | firmware |

## Decisions locked

- **Transport:** one `wss` WebSocket, ESP32 ↔ gateway. Gateway ↔ butler over the internal network.
- **Screen:** structured cards. Claude calls `display_on_device({op, title, rows[], icon, color, meter?})`; firmware renders with LVGL widgets.
- **Trigger:** target = open-mic + **server-side** VAD (reuse vector-llm). Bring-up = push-to-talk (touch) to de-risk, then switch to VAD.
- **Auth / user selection:** ESP32 holds only a **gateway device token** (in NVS), never the butler key. On WS handshake it sends `{device_token, user_id}`. Gateway validates the token, then calls butler with `X-API-Key=INTERNAL_API_KEY` + that `user_id`. Selecting a user = picking `user_id` from a touch list → per-user memory just works.
- **STT:** Groq Whisper in the gateway (matches butler agent, low CPU on the already-busy Mac Mini). faster-whisper (local/private) is the fallback option.
- **Audio on the wire:** raw PCM 16kHz mono for v1 (simple). Opus is a later bandwidth optimization. On-device energy gate so uplink only streams when there's sound.

## Build phases (small, each independently testable)

1. **Firmware: WiFi + WSS echo.** Add WiFi STA + NVS creds to firmware; connect `esp_websocket_client` (WSS) to a stub gateway; round-trip a text ping. *Test: device shows "connected", echoes a message.*
2. **Gateway skeleton.** New asyncio WS server in `claude-esp`; accepts `{device_token, user_id}`; for now echoes audio and forwards a hardcoded card. Add Cloudflare Tunnel route `esp-gateway.<domain>`. *Test: device reaches it over the internet.*
3. **Firmware: audio playback.** Call `bsp_audio_codec_speaker_init()`, I2S TX + jitter buffer; play PCM frames the gateway sends. *Test: gateway pushes a TTS clip, device speaks it.*
4. **Firmware: audio capture (push-to-talk).** Call `bsp_audio_codec_microphone_init()`, I2S RX; on touch-hold, stream PCM up. *Test: gateway logs received audio.*
5. **Gateway: full voice loop.** Wire STT (Groq) → butler `/api/voice/stream` (with `user_id`) → Kokoro TTS → PCM down. Reuse vector-llm's resampler. *Test: hold-to-talk, hear Claude reply, memory persists per user.*
6. **Screen cards.** Add butler `display_on_device` tool + card SSE event; gateway forwards card events; firmware LVGL card renderer. *Test: "show me the weather" draws a card.*
7. **Open-mic VAD.** Move from push-to-talk to continuous stream + gateway-side Silero VAD/segmentation (port/share vector-llm `stt.py`). On-device energy gate to save uplink. *Test: hands-free conversation.*
8. **User picker + provisioning UI.** Touch screen to choose user and (first-run) enter WiFi/gateway config; persist in NVS.

## Open questions / risks

- **vector-llm refactor depth:** copy its VAD/segmenter into the gateway (fast) vs. extract a shared `voice_pipeline` module both import (cleaner). Start with copy.
- **TLS on device:** use `esp_crt_bundle` for the Cloudflare cert chain (no per-cert pinning needed).
- **Bandwidth:** raw PCM ≈ 256 kbps each way continuous. Fine on broadband; revisit with Opus if it's used on the move.
- **"Butler" visual identity:** optional — reuse the pet renderer as an animated avatar alongside cards, or keep a minimal status face. Cosmetic, defer.
- **Where the gateway lives:** new container on the `homeserver` Docker network is the natural home (sits next to butler-api), even though the code is developed in `claude-esp`.
