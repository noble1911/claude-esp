#include "ui.h"

#include <stdio.h>
#include <string.h>

#include "bsp/esp32_s3_touch_amoled_1_8.h"
#include "esp_log.h"
#include "lvgl.h"

#include "audio.h"
#include "ws_client.h"

static const char *TAG = "ui";

static lv_obj_t *s_status;
static lv_obj_t *s_card_panel;
static lv_obj_t *s_card_title;
static lv_obj_t *s_card_body;
static lv_obj_t *s_card_bar;
static lv_obj_t *s_talk;
static lv_obj_t *s_talk_label;

// Push-to-talk. Runs inside the LVGL event context (lock already held), so it
// must NOT take the display lock again.
static void talk_cb(lv_event_t *e) {
    lv_event_code_t code = lv_event_get_code(e);
    if (code == LV_EVENT_PRESSED) {
        audio_set_capture(true);
        ws_send_audio_start();
        lv_label_set_text(s_talk_label, "Listening…");
    } else if (code == LV_EVENT_RELEASED) {
        audio_set_capture(false);
        ws_send_audio_end();
        lv_label_set_text(s_talk_label, "Hold to talk");
    }
}

void ui_init(void) {
    if (!bsp_display_lock(1000)) {
        ESP_LOGE(TAG, "display lock failed");
        return;
    }
    lv_obj_t *scr = lv_screen_active();
    lv_obj_set_style_bg_color(scr, lv_color_hex(0x0b0f17), 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);

    s_status = lv_label_create(scr);
    lv_label_set_text(s_status, "starting…");
    lv_obj_set_style_text_color(s_status, lv_color_hex(0x9aa4b2), 0);
    lv_obj_align(s_status, LV_ALIGN_TOP_MID, 0, 8);

    s_card_panel = lv_obj_create(scr);
    lv_obj_set_size(s_card_panel, BSP_LCD_H_RES - 24, 260);
    lv_obj_align(s_card_panel, LV_ALIGN_TOP_MID, 0, 40);
    lv_obj_set_style_bg_color(s_card_panel, lv_color_hex(0x111827), 0);
    lv_obj_set_style_radius(s_card_panel, 12, 0);
    lv_obj_set_style_border_width(s_card_panel, 0, 0);
    lv_obj_remove_flag(s_card_panel, LV_OBJ_FLAG_SCROLLABLE);

    s_card_title = lv_label_create(s_card_panel);
    lv_label_set_text(s_card_title, "");
    lv_obj_set_style_text_color(s_card_title, lv_color_hex(0xffffff), 0);
    lv_obj_align(s_card_title, LV_ALIGN_TOP_LEFT, 0, 0);

    s_card_body = lv_label_create(s_card_panel);
    lv_label_set_long_mode(s_card_body, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_card_body, BSP_LCD_H_RES - 56);
    lv_label_set_text(s_card_body, "");
    lv_obj_set_style_text_color(s_card_body, lv_color_hex(0xd1d5db), 0);
    lv_obj_align(s_card_body, LV_ALIGN_TOP_LEFT, 0, 30);

    s_card_bar = lv_bar_create(s_card_panel);
    lv_obj_set_size(s_card_bar, BSP_LCD_H_RES - 56, 12);
    lv_obj_align(s_card_bar, LV_ALIGN_BOTTOM_LEFT, 0, -6);
    lv_bar_set_range(s_card_bar, 0, 100);
    lv_bar_set_value(s_card_bar, 0, LV_ANIM_OFF);
    lv_obj_add_flag(s_card_bar, LV_OBJ_FLAG_HIDDEN);

    // Hold-to-talk control. A plain clickable object (not lv_button) keeps the
    // press/release handling simple and version-robust.
    s_talk = lv_obj_create(scr);
    lv_obj_set_size(s_talk, BSP_LCD_H_RES - 40, 96);
    lv_obj_align(s_talk, LV_ALIGN_BOTTOM_MID, 0, -16);
    lv_obj_set_style_bg_color(s_talk, lv_color_hex(0x2563eb), 0);
    lv_obj_set_style_radius(s_talk, 16, 0);
    lv_obj_add_flag(s_talk, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(s_talk, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(s_talk, talk_cb, LV_EVENT_PRESSED, NULL);
    lv_obj_add_event_cb(s_talk, talk_cb, LV_EVENT_RELEASED, NULL);

    s_talk_label = lv_label_create(s_talk);
    lv_label_set_text(s_talk_label, "Hold to talk");
    lv_obj_set_style_text_color(s_talk_label, lv_color_hex(0xffffff), 0);
    lv_obj_center(s_talk_label);

    bsp_display_unlock();
}

void ui_set_status(const char *text) {
    if (!s_status) return;
    if (bsp_display_lock(200)) {
        lv_label_set_text(s_status, text);
        bsp_display_unlock();
    }
}

void ui_show_card(const cJSON *card) {
    if (!s_card_panel || !card) return;
    const cJSON *op = cJSON_GetObjectItem(card, "op");
    const char *ops = cJSON_IsString(op) ? op->valuestring : "card";

    if (!bsp_display_lock(300)) return;

    if (strcmp(ops, "clear") == 0) {
        lv_label_set_text(s_card_title, "");
        lv_label_set_text(s_card_body, "");
        lv_obj_add_flag(s_card_bar, LV_OBJ_FLAG_HIDDEN);
        bsp_display_unlock();
        return;
    }

    const cJSON *title = cJSON_GetObjectItem(card, "title");
    lv_label_set_text(s_card_title, cJSON_IsString(title) ? title->valuestring : "");

    // Build the body from subtitle + rows[] + body + message.
    char body[512];
    size_t bl = 0;
    body[0] = '\0';
    const cJSON *subtitle = cJSON_GetObjectItem(card, "subtitle");
    if (cJSON_IsString(subtitle)) {
        bl += snprintf(body + bl, sizeof(body) - bl, "%s\n", subtitle->valuestring);
    }
    const cJSON *rows = cJSON_GetObjectItem(card, "rows");
    if (cJSON_IsArray(rows)) {
        const cJSON *it;
        cJSON_ArrayForEach(it, rows) {
            if (cJSON_IsString(it) && bl < sizeof(body) - 2) {
                bl += snprintf(body + bl, sizeof(body) - bl, "%s\n", it->valuestring);
            }
        }
    }
    const cJSON *bodyj = cJSON_GetObjectItem(card, "body");
    if (cJSON_IsString(bodyj) && bl < sizeof(body) - 2) {
        bl += snprintf(body + bl, sizeof(body) - bl, "%s", bodyj->valuestring);
    }
    const cJSON *msg = cJSON_GetObjectItem(card, "message");
    if (cJSON_IsString(msg) && bl < sizeof(body) - 2) {
        bl += snprintf(body + bl, sizeof(body) - bl, "%s", msg->valuestring);
    }
    lv_label_set_text(s_card_body, body);

    const cJSON *meter = cJSON_GetObjectItem(card, "meter");
    if (cJSON_IsObject(meter)) {
        const cJSON *val = cJSON_GetObjectItem(meter, "value");
        if (cJSON_IsNumber(val)) {
            int pct = (int)(val->valuedouble * 100.0);
            if (pct < 0) pct = 0;
            if (pct > 100) pct = 100;
            lv_bar_set_value(s_card_bar, pct, LV_ANIM_OFF);
            lv_obj_remove_flag(s_card_bar, LV_OBJ_FLAG_HIDDEN);
        }
    } else {
        lv_obj_add_flag(s_card_bar, LV_OBJ_FLAG_HIDDEN);
    }

    const cJSON *accent = cJSON_GetObjectItem(card, "accent");
    if (cJSON_IsString(accent)) {
        unsigned int rgb = 0;
        if (sscanf(accent->valuestring, "#%06x", &rgb) == 1) {
            lv_obj_set_style_text_color(s_card_title, lv_color_hex(rgb), 0);
        }
    }

    bsp_display_unlock();
}
