#pragma once
#include "esp_err.h"

// Runtime configuration, persisted in NVS (namespace "claude_esp").
// First-boot defaults come from compile-time macros (see app_config.c); the
// on-device settings UI (or a future provisioning flow) can overwrite them.
typedef struct {
    char wifi_ssid[33];
    char wifi_pass[65];
    char gateway_uri[160];   // ws://host:port/ws  or  wss://host/ws
    char device_token[96];   // shared secret presented to the gateway
    char user_id[64];        // butler user to converse as
} app_config_t;

void app_config_load(app_config_t *out);
esp_err_t app_config_save(const app_config_t *cfg);
