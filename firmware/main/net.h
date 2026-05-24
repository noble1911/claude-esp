#pragma once
#include <stdbool.h>
#include "esp_err.h"

// Bring up WiFi STA and auto-reconnect. Returns immediately; use
// net_wifi_is_connected() (or wait on the netif) to know when IP is up.
esp_err_t net_wifi_start(const char *ssid, const char *pass);
bool net_wifi_is_connected(void);
