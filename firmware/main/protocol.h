#pragma once
// Device <-> gateway message type strings. See claude-esp/PROTOCOL.md.

// Device -> gateway
#define MSG_HELLO        "hello"
#define MSG_AUDIO_START  "audio_start"
#define MSG_AUDIO_END    "audio_end"
#define MSG_TEXT         "text"
#define MSG_SET_USER     "set_user"
#define MSG_CANCEL       "cancel"
#define MSG_PING         "ping"

// Gateway -> device
#define MSG_READY        "ready"
#define MSG_STATE        "state"
#define MSG_STT          "stt"
#define MSG_SAY          "say"
#define MSG_TTS_START    "tts_start"
#define MSG_TTS_END      "tts_end"
#define MSG_CARD         "card"
#define MSG_IMAGE        "image"
#define MSG_USERS        "users"
#define MSG_ERROR        "error"
#define MSG_PONG         "pong"

#define PROTO_VERSION    1

// Audio: PCM signed-16 LE mono, 16 kHz both directions (see PROTOCOL.md).
#define AUDIO_RATE_HZ    16000
