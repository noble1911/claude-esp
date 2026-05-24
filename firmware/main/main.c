#include <stddef.h>
#include <stdint.h>

#include "bsp/esp32_s3_touch_amoled_1_8.h"
#include "esp_log.h"
#include "nvs_flash.h"

#include "app_config.h"
#include "audio.h"
#include "net.h"
#include "ui.h"
#include "ws_client.h"

static const char *TAG = "main";

// Captured mic frames go straight up the WebSocket as binary.
static void on_mic_frame(const uint8_t *pcm, size_t len) {
    ws_send_binary(pcm, len);
}

void app_main(void) {
    esp_err_t e = nvs_flash_init();
    if (e == ESP_ERR_NVS_NO_FREE_PAGES || e == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }

    static app_config_t cfg;
    app_config_load(&cfg);

    // Display + LVGL (BSP starts the esp_lvgl_port task internally).
    bsp_display_start();
    ui_init();
    ui_set_status("starting…");

    // Audio (ES8311 mic + speaker).
    if (audio_init() == ESP_OK) {
        audio_set_mic_callback(on_mic_frame);
    } else {
        ESP_LOGE(TAG, "audio init failed — continuing without audio");
    }

    // Network + gateway link.
    net_wifi_start(cfg.wifi_ssid, cfg.wifi_pass);
    ws_start(&cfg);

    ESP_LOGI(TAG, "setup complete");
}
