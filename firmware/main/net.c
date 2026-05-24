#include "net.h"

#include <stdlib.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "net";
static volatile bool s_connected = false;

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        // initial connect is kicked off by scan_then_connect_task after the boot scan
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        s_connected = false;
        wifi_event_sta_disconnected_t *d = (wifi_event_sta_disconnected_t *)data;
        ESP_LOGW(TAG, "wifi disconnected (reason=%d) — reconnecting", d ? d->reason : -1);
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        s_connected = true;
        ip_event_got_ip_t *e = (ip_event_got_ip_t *)data;
        ESP_LOGI(TAG, "got ip: " IPSTR, IP2STR(&e->ip_info.ip));
    }
}

// One-shot at boot: scan and log every AP the device can see from its current
// position (real SSIDs + RSSI), then start connecting. This is the device's own
// view of available networks — what the Mac can't show.
static void scan_then_connect_task(void *arg) {
    vTaskDelay(pdMS_TO_TICKS(800));  // let the STA interface finish starting
    wifi_scan_config_t sc = {0};
    if (esp_wifi_scan_start(&sc, true) == ESP_OK) {
        uint16_t n = 0;
        esp_wifi_scan_get_ap_num(&n);
        if (n > 24) n = 24;
        wifi_ap_record_t *recs = calloc(n ? n : 1, sizeof(wifi_ap_record_t));
        if (recs && esp_wifi_scan_get_ap_records(&n, recs) == ESP_OK) {
            ESP_LOGW(TAG, "==== WiFi scan: %u AP(s) visible from device ====", n);
            for (int i = 0; i < n; i++) {
                ESP_LOGW(TAG, "  RSSI=%4d  ch=%2d  ssid=%s",
                         recs[i].rssi, recs[i].primary, (char *)recs[i].ssid);
            }
        }
        free(recs);
    } else {
        ESP_LOGW(TAG, "wifi scan failed");
    }
    esp_wifi_connect();
    vTaskDelete(NULL);
}

// Periodically log the link RSSI so we can see signal strength at the device's
// actual location (> -67 solid, -70..-80 flaky, < -80 unusable).
static void rssi_task(void *arg) {
    wifi_ap_record_t ap;
    for (;;) {
        if (s_connected && esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
            ESP_LOGI(TAG, "wifi link: RSSI=%d dBm  ch=%d  ssid=%s",
                     ap.rssi, ap.primary, (char *)ap.ssid);
        }
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
}

esp_err_t net_wifi_start(const char *ssid, const char *pass) {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, on_event, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, NULL, NULL));

    wifi_config_t wc = {0};
    strlcpy((char *)wc.sta.ssid, ssid, sizeof(wc.sta.ssid));
    strlcpy((char *)wc.sta.password, pass, sizeof(wc.sta.password));
    wc.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    ESP_ERROR_CHECK(esp_wifi_start());
    // Disable WiFi power save. Modem-sleep parks the radio between beacons,
    // which stalls a latency-sensitive bidirectional audio stream (missed
    // block-acks, ping timeouts) and causes periodic WS reconnects. Keep it awake.
    esp_wifi_set_ps(WIFI_PS_NONE);
    xTaskCreate(scan_then_connect_task, "wifi_scan", 4096, NULL, 4, NULL);
    xTaskCreate(rssi_task, "wifi_rssi", 3072, NULL, 3, NULL);
    ESP_LOGI(TAG, "wifi starting (power-save off), SSID='%s'", ssid);
    return ESP_OK;
}

bool net_wifi_is_connected(void) { return s_connected; }
