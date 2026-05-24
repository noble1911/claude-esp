#include "audio.h"

#include <string.h>

#include "bsp/esp32_s3_touch_amoled_1_8.h"
#include "esp_codec_dev.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/stream_buffer.h"
#include "freertos/task.h"
#include "protocol.h"

static const char *TAG = "audio";

#define MIC_FRAME_BYTES   640      // 20 ms of PCM16 mono @ 16 kHz
#define PLAY_CHUNK_BYTES  640
#define PLAY_BUFFER_BYTES (48 * 1024)

static esp_codec_dev_handle_t s_spk;
static esp_codec_dev_handle_t s_mic;
static StreamBufferHandle_t s_play;
static volatile bool s_capture;
static audio_mic_cb_t s_cb;

static void play_task(void *arg) {
    uint8_t buf[PLAY_CHUNK_BYTES];
    uint8_t silence[PLAY_CHUNK_BYTES];
    memset(silence, 0, sizeof(silence));
    int idle_frames = 0;
    for (;;) {
        size_t n = xStreamBufferReceive(s_play, buf, sizeof(buf), pdMS_TO_TICKS(20));
        if (n > 0 && s_spk) {
            esp_codec_dev_write(s_spk, buf, n);
            idle_frames = 0;
        } else if (s_spk && idle_frames < 75) {
            // Buffer underrun during/just after audio: feed silence so the I2S DMA
            // never replays stale samples (the "robotic" artifact on jittery WiFi).
            // Stop after ~1.5 s of silence so we don't hiss while truly idle.
            esp_codec_dev_write(s_spk, silence, sizeof(silence));
            idle_frames++;
        }
    }
}

static void mic_task(void *arg) {
    uint8_t buf[MIC_FRAME_BYTES];
    for (;;) {
        if (!s_mic) {
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }
        int r = esp_codec_dev_read(s_mic, buf, sizeof(buf));
        if (r == ESP_CODEC_DEV_OK) {
            if (s_capture && s_cb) {
                s_cb(buf, sizeof(buf));
            }
        } else {
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }
}

esp_err_t audio_init(void) {
    // Create the duplex I2S channel, then attach the ES8311 in/out devices.
    ESP_ERROR_CHECK(bsp_audio_init(NULL));
    s_spk = bsp_audio_codec_speaker_init();
    s_mic = bsp_audio_codec_microphone_init();
    if (!s_spk || !s_mic) {
        ESP_LOGE(TAG, "codec init failed (spk=%p mic=%p)", s_spk, s_mic);
        return ESP_FAIL;
    }

    esp_codec_dev_sample_info_t fs = {
        .bits_per_sample = 16,
        .channel = 1,
        .channel_mask = 0,
        .sample_rate = AUDIO_RATE_HZ,
        .mclk_multiple = 0,
    };
    esp_codec_dev_set_out_vol(s_spk, 80);
    if (esp_codec_dev_open(s_spk, &fs) != ESP_CODEC_DEV_OK) {
        ESP_LOGW(TAG, "speaker open failed");
    }
    if (esp_codec_dev_open(s_mic, &fs) != ESP_CODEC_DEV_OK) {
        ESP_LOGW(TAG, "microphone open failed");
    }

    s_play = xStreamBufferCreate(PLAY_BUFFER_BYTES, 1);
    if (!s_play) return ESP_ERR_NO_MEM;

    // Pin audio to core 1 (APP_CPU) so the WiFi/network stack on core 0 can't
    // preempt playback and inject jitter into the stream.
    xTaskCreatePinnedToCore(play_task, "audio_play", 4096, NULL, 6, NULL, 1);
    xTaskCreatePinnedToCore(mic_task, "audio_mic", 4096, NULL, 5, NULL, 1);
    ESP_LOGI(TAG, "audio ready (PCM16 mono @ %d Hz)", AUDIO_RATE_HZ);
    return ESP_OK;
}

void audio_play_pcm(const uint8_t *data, size_t len) {
    if (s_play && len) {
        xStreamBufferSend(s_play, data, len, 0);
    }
}

void audio_set_capture(bool enabled) { s_capture = enabled; }

void audio_set_mic_callback(audio_mic_cb_t cb) { s_cb = cb; }
