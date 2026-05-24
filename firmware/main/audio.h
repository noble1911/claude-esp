#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

// PCM16 mono @ AUDIO_RATE_HZ both directions.
typedef void (*audio_mic_cb_t)(const uint8_t *pcm, size_t len);

// Bring up the ES8311 codec (speaker + mic) and start playback/capture tasks.
esp_err_t audio_init(void);

// Enqueue TTS PCM bytes for the speaker (non-blocking; drops if buffer full).
void audio_play_pcm(const uint8_t *data, size_t len);

// Push-to-talk gate: when true, captured mic frames are delivered to the callback.
void audio_set_capture(bool enabled);

// Register the sink for captured mic frames (the WS client sends them upstream).
void audio_set_mic_callback(audio_mic_cb_t cb);

// Speaker output volume, 0..100 (applied immediately if the codec is open).
void audio_set_volume(int vol);
int audio_get_volume(void);
