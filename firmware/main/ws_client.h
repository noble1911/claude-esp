#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "app_config.h"

void ws_start(const app_config_t *cfg);
bool ws_is_connected(void);

// Device -> gateway control messages.
void ws_send_audio_start(void);
void ws_send_audio_end(void);
void ws_send_binary(const uint8_t *data, size_t len);  // mic PCM frames
void ws_send_text_input(const char *text);             // typed/dev input
void ws_send_set_user(const char *user_id);
void ws_send_cancel(void);
