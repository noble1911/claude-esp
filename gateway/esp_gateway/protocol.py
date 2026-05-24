"""Message type constants for the device<->gateway protocol. See PROTOCOL.md."""

from __future__ import annotations

# Device -> gateway
HELLO = "hello"
AUDIO_START = "audio_start"
AUDIO_END = "audio_end"
TEXT = "text"
SET_USER = "set_user"
CANCEL = "cancel"
PING = "ping"

# Gateway -> device
READY = "ready"
STATE = "state"
STT = "stt"
SAY = "say"
TTS_START = "tts_start"
TTS_END = "tts_end"
CARD = "card"
IMAGE = "image"  # full-screen image: {"type":"image","format":"png","data":"<base64>"}
USERS = "users"
ERROR = "error"
PONG = "pong"

# state values
STATE_IDLE = "idle"
STATE_LISTENING = "listening"
STATE_THINKING = "thinking"
STATE_SPEAKING = "speaking"

PROTO_VERSION = 1
