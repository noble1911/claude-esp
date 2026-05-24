"""esp-gateway: bridges the ESP32 voice device to the home-server butler brain.

Device  --wss-->  gateway  -->  Groq STT
                     |-->  butler /api/voice/stream  (Claude + per-user memory + tools)
                     |-->  Kokoro TTS
                     `-->  PCM audio + screen cards back to the device

See PROTOCOL.md for the device<->gateway WebSocket contract.
"""

__version__ = "0.1.0"
