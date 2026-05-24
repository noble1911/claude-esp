#include "ws_client.h"

#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"

#include "audio.h"
#include "protocol.h"
#include "ui.h"

static const char *TAG = "ws";

static esp_websocket_client_handle_t s_client;
static app_config_t s_cfg;
static volatile bool s_connected;

// Reassembly buffer for (possibly fragmented) text messages.
static char *s_rx;
static size_t s_rxcap, s_rxlen;
static int s_rxop;  // op_code of the message currently being reassembled

// ── outbound ────────────────────────────────────────────────────────
static void send_text(const char *s) {
    if (s_client && esp_websocket_client_is_connected(s_client)) {
        esp_websocket_client_send_text(s_client, s, strlen(s), pdMS_TO_TICKS(2000));
    }
}

static void send_simple(const char *type) {
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "type", type);
    char *s = cJSON_PrintUnformatted(o);
    send_text(s);
    cJSON_free(s);
    cJSON_Delete(o);
}

static void send_hello(void) {
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "type", MSG_HELLO);
    cJSON_AddNumberToObject(o, "proto", PROTO_VERSION);
    cJSON_AddStringToObject(o, "device_token", s_cfg.device_token);
    cJSON_AddStringToObject(o, "user_id", s_cfg.user_id);
    cJSON *cap = cJSON_AddObjectToObject(o, "capture");
    cJSON_AddNumberToObject(cap, "rate", AUDIO_RATE_HZ);
    cJSON *pb = cJSON_AddObjectToObject(o, "playback");
    cJSON_AddNumberToObject(pb, "rate", AUDIO_RATE_HZ);
    char *s = cJSON_PrintUnformatted(o);
    send_text(s);
    cJSON_free(s);
    cJSON_Delete(o);
    ESP_LOGI(TAG, "sent hello (user=%s)", s_cfg.user_id);
}

void ws_send_audio_start(void) { send_simple(MSG_AUDIO_START); }
void ws_send_audio_end(void) { send_simple(MSG_AUDIO_END); }
void ws_send_cancel(void) { send_simple(MSG_CANCEL); }

void ws_send_binary(const uint8_t *data, size_t len) {
    if (s_client && esp_websocket_client_is_connected(s_client)) {
        esp_websocket_client_send_bin(s_client, (const char *)data, (int)len, pdMS_TO_TICKS(1000));
    }
}

void ws_send_text_input(const char *text) {
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "type", MSG_TEXT);
    cJSON_AddStringToObject(o, "text", text);
    char *s = cJSON_PrintUnformatted(o);
    send_text(s);
    cJSON_free(s);
    cJSON_Delete(o);
}

void ws_send_set_user(const char *user_id) {
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "type", MSG_SET_USER);
    cJSON_AddStringToObject(o, "user_id", user_id);
    char *s = cJSON_PrintUnformatted(o);
    send_text(s);
    cJSON_free(s);
    cJSON_Delete(o);
    strlcpy(s_cfg.user_id, user_id, sizeof(s_cfg.user_id));
}

// ── inbound ─────────────────────────────────────────────────────────
static void handle_json(const char *buf, size_t len) {
    cJSON *root = cJSON_ParseWithLength(buf, len);
    if (!root) {
        ESP_LOGW(TAG, "bad json (%u bytes)", (unsigned)len);
        return;
    }
    const cJSON *t = cJSON_GetObjectItemCaseSensitive(root, "type");
    if (cJSON_IsString(t)) {
        const char *type = t->valuestring;
        if (strcmp(type, MSG_STATE) == 0) {
            const cJSON *v = cJSON_GetObjectItem(root, "value");
            if (cJSON_IsString(v)) ui_set_status(v->valuestring);
        } else if (strcmp(type, MSG_CARD) == 0) {
            const cJSON *c = cJSON_GetObjectItem(root, "card");
            ESP_LOGI(TAG, "card message received (%s)", c ? "rendering" : "MISSING card field");
            if (c) ui_show_card(c);
        } else if (strcmp(type, MSG_IMAGE) == 0) {
            // {"type":"image","format":"png","data":"<base64>"} → decode + display.
            const cJSON *data = cJSON_GetObjectItem(root, "data");
            if (cJSON_IsString(data)) {
                size_t b64len = strlen(data->valuestring);
                size_t cap = (b64len / 4) * 3 + 4;  // upper bound on decoded size
                uint8_t *png = heap_caps_malloc(cap, MALLOC_CAP_SPIRAM);
                if (png) {
                    size_t olen = 0;
                    int rc = mbedtls_base64_decode(png, cap, &olen,
                                                   (const unsigned char *)data->valuestring, b64len);
                    if (rc == 0 && olen > 0) {
                        ESP_LOGI(TAG, "image received: %u B png", (unsigned)olen);
                        ui_show_image(png, olen);  // copies into its own buffer
                    } else {
                        ESP_LOGW(TAG, "image base64 decode failed (rc=%d)", rc);
                    }
                    free(png);
                } else {
                    ESP_LOGE(TAG, "image: no PSRAM for %u B", (unsigned)cap);
                }
            }
        } else if (strcmp(type, MSG_READY) == 0) {
            ui_set_status("ready");
            ESP_LOGI(TAG, "gateway ready");
        } else if (strcmp(type, MSG_ERROR) == 0) {
            const cJSON *m = cJSON_GetObjectItem(root, "message");
            ESP_LOGE(TAG, "gateway error: %s", cJSON_IsString(m) ? m->valuestring : "?");
            ui_set_status("error");
        } else {
            ESP_LOGD(TAG, "msg type=%s", type);
        }
    }
    cJSON_Delete(root);
}

static void on_ws(void *arg, esp_event_base_t base, int32_t id, void *ev) {
    esp_websocket_event_data_t *d = (esp_websocket_event_data_t *)ev;
    switch (id) {
    case WEBSOCKET_EVENT_CONNECTED:
        s_connected = true;
        ESP_LOGI(TAG, "ws connected");
        send_hello();
        break;
    case WEBSOCKET_EVENT_DISCONNECTED:
        s_connected = false;
        ESP_LOGW(TAG, "ws disconnected");
        // Reset device state so a mid-turn drop doesn't leave us stuck capturing /
        // showing "Listening…". State resumes from the gateway on reconnect.
        audio_set_capture(false);
        ui_reset_talk();
        ui_set_status("offline");
        break;
    case WEBSOCKET_EVENT_DATA: {
        int op = d->op_code;
        // Binary audio (0x2) + continuation frames while reassembling binary.
        if (op == 0x02 || (op == 0x00 && s_rxop == 0x02)) {
            if (d->data_len > 0) {
                audio_play_pcm((const uint8_t *)d->data_ptr, d->data_len);
            }
            s_rxop = (d->payload_offset + d->data_len >= d->payload_len) ? 0 : 0x02;
            break;
        }
        // Text (0x1) + continuation; reassemble then parse.
        if (op == 0x01 || (op == 0x00 && s_rxop == 0x01)) {
            size_t need = s_rxlen + d->data_len + 1;
            if (need > s_rxcap) {
                size_t ncap = need < 256 ? 256 : need;
                // PSRAM: image messages (base64 PNG) can be tens of KB — keep them
                // off the internal heap that TLS + WiFi already lean on.
                char *n = heap_caps_realloc(s_rx, ncap, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
                if (!n) {
                    s_rxlen = 0;
                    s_rxop = 0;
                    break;
                }
                s_rx = n;
                s_rxcap = ncap;
            }
            memcpy(s_rx + s_rxlen, d->data_ptr, d->data_len);
            s_rxlen += d->data_len;
            if (d->payload_offset + d->data_len >= d->payload_len) {
                s_rx[s_rxlen] = '\0';
                handle_json(s_rx, s_rxlen);
                s_rxlen = 0;
                s_rxop = 0;
            } else {
                s_rxop = 0x01;
            }
            break;
        }
        break;
    }
    default:
        break;
    }
}

void ws_start(const app_config_t *cfg) {
    s_cfg = *cfg;
    esp_websocket_client_config_t wcfg = {
        .uri = s_cfg.gateway_uri,
        .reconnect_timeout_ms = 2000,   // reconnect fast so an offline blip is brief
        .network_timeout_ms = 10000,
        .buffer_size = 4096,
        // TCP keepalive (kernel-level, no extra WS writes): detect a silently-dead
        // connection on a flaky link in ~20 s instead of waiting for the next write.
        .keep_alive_enable = true,
        .keep_alive_idle = 5,
        .keep_alive_interval = 5,
        .keep_alive_count = 3,
    };
#if defined(CONFIG_MBEDTLS_CERTIFICATE_BUNDLE)
    wcfg.crt_bundle_attach = esp_crt_bundle_attach;  // validate wss:// via root bundle
#endif
    s_client = esp_websocket_client_init(&wcfg);
    if (!s_client) {
        ESP_LOGE(TAG, "ws init failed");
        return;
    }
    esp_websocket_register_events(s_client, WEBSOCKET_EVENT_ANY, on_ws, NULL);
    esp_websocket_client_start(s_client);
    ESP_LOGI(TAG, "ws connecting to %s", s_cfg.gateway_uri);
}

bool ws_is_connected(void) { return s_connected; }
