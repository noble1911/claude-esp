#pragma once
#include <stddef.h>
#include <stdint.h>

#include "cJSON.h"

// All functions are safe to call from non-LVGL tasks (they take the BSP display
// lock internally), except when already inside an LVGL event callback.
void ui_init(void);
void ui_set_status(const char *text);       // top status line (connection / state)
void ui_show_card(const cJSON *card);        // render a device_card (see PROTOCOL.md)
void ui_show_image(const uint8_t *png, size_t len);  // full-screen PNG (server-rendered SVG)
