#include "ui.h"

#include <stdio.h>
#include <string.h>
#include <strings.h>  // strcasecmp

#include "bsp/esp32_s3_touch_amoled_1_8.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "lvgl.h"

#include "audio.h"
#include "power.h"
#include "ws_client.h"

static const char *TAG = "ui";

static lv_obj_t *s_status;       // top status line (connection / state)
static lv_obj_t *s_battery;      // small battery indicator, top-right corner
static lv_obj_t *s_card;         // card content panel (flex column, rebuilt per card)
static lv_obj_t *s_talk;         // hold-to-talk button
static lv_obj_t *s_talk_label;
static lv_obj_t *s_toast;        // transient overlay banner
static lv_obj_t *s_toast_label;
static lv_obj_t *s_image;          // server-rendered image, fills the card area
static lv_image_dsc_t s_img_dsc[2];// two descriptors, alternated so set_src always refreshes
static int s_img_slot;
static uint8_t *s_img_buf;         // displayed PNG bytes (PSRAM) the decoder reads from
static size_t s_img_len;
static uint8_t *s_img_next;        // staged PNG awaiting the LVGL task
static size_t s_img_next_len;

#define COL_TITLE   lv_color_hex(0xffffff)
#define COL_DIM     lv_color_hex(0x9aa4b2)
#define COL_BODY    lv_color_hex(0xd1d5db)
#define COL_ACCENT  lv_color_hex(0x3b82f6)

// ── helpers ─────────────────────────────────────────────────────────
static lv_color_t parse_hex(const cJSON *obj, const char *key, lv_color_t def) {
    const cJSON *a = obj ? cJSON_GetObjectItem(obj, key) : NULL;
    if (cJSON_IsString(a)) {
        unsigned int rgb = 0;
        if (sscanf(a->valuestring, "#%06x", &rgb) == 1) return lv_color_hex(rgb);
    }
    return def;
}

// Map a card icon name → an LVGL built-in symbol glyph (NULL if unsupported).
static const char *icon_symbol(const char *name) {
    if (!name) return NULL;
    static const struct { const char *n; const char *s; } m[] = {
        {"check", LV_SYMBOL_OK}, {"ok", LV_SYMBOL_OK},
        {"warning", LV_SYMBOL_WARNING}, {"alert", LV_SYMBOL_WARNING},
        {"info", LV_SYMBOL_BELL}, {"bell", LV_SYMBOL_BELL},
        {"music", LV_SYMBOL_AUDIO}, {"audio", LV_SYMBOL_AUDIO},
        {"media", LV_SYMBOL_VIDEO}, {"video", LV_SYMBOL_VIDEO},
        {"home", LV_SYMBOL_HOME},
        {"mail", LV_SYMBOL_ENVELOPE}, {"email", LV_SYMBOL_ENVELOPE},
        {"image", LV_SYMBOL_IMAGE}, {"photo", LV_SYMBOL_IMAGE},
        {"wifi", LV_SYMBOL_WIFI}, {"battery", LV_SYMBOL_BATTERY_FULL},
        {"location", LV_SYMBOL_GPS}, {"gps", LV_SYMBOL_GPS},
        {"settings", LV_SYMBOL_SETTINGS}, {"list", LV_SYMBOL_LIST},
        {"calendar", LV_SYMBOL_LIST}, {"refresh", LV_SYMBOL_REFRESH},
        {"play", LV_SYMBOL_PLAY}, {"pause", LV_SYMBOL_PAUSE},
        {"download", LV_SYMBOL_DOWNLOAD}, {"upload", LV_SYMBOL_UPLOAD},
        {"power", LV_SYMBOL_POWER}, {"charge", LV_SYMBOL_CHARGE},
        {"phone", LV_SYMBOL_CALL}, {"call", LV_SYMBOL_CALL},
        {"file", LV_SYMBOL_FILE},
    };
    for (size_t i = 0; i < sizeof(m) / sizeof(m[0]); i++) {
        if (strcasecmp(name, m[i].n) == 0) return m[i].s;
    }
    return NULL;
}

static lv_obj_t *add_wrapped_label(const char *txt, lv_color_t color) {
    lv_obj_t *l = lv_label_create(s_card);
    lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(l, lv_pct(100));
    lv_label_set_text(l, txt ? txt : "");
    lv_obj_set_style_text_color(l, color, 0);
    return l;
}

// Title line: "[icon]  Title", coloured by accent.
static void add_title(const cJSON *card, lv_color_t accent) {
    const cJSON *title = cJSON_GetObjectItem(card, "title");
    const cJSON *icon = cJSON_GetObjectItem(card, "icon");
    const char *sym = cJSON_IsString(icon) ? icon_symbol(icon->valuestring) : NULL;
    if (!cJSON_IsString(title) && !sym) return;
    char buf[176];
    snprintf(buf, sizeof(buf), "%s%s%s", sym ? sym : "", sym ? "  " : "",
             cJSON_IsString(title) ? title->valuestring : "");
    lv_obj_t *t = add_wrapped_label(buf, accent);
    lv_obj_set_style_text_font(t, &lv_font_montserrat_20, 0);  // title stands out
}

// Key/value row: label on the left (dim), value on the right (bright).
static void add_field(const char *label, const char *value) {
    lv_obj_t *row = lv_obj_create(s_card);
    lv_obj_remove_style_all(row);
    lv_obj_set_width(row, lv_pct(100));
    lv_obj_set_height(row, LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_SPACE_BETWEEN, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_t *k = lv_label_create(row);
    lv_label_set_text(k, label ? label : "");
    lv_obj_set_style_text_color(k, COL_DIM, 0);
    lv_obj_t *v = lv_label_create(row);
    lv_label_set_text(v, value ? value : "");
    lv_obj_set_style_text_color(v, COL_TITLE, 0);
}

// Labelled progress meter (value 0..1), filled in the accent colour.
static void add_meter(const char *label, double value, lv_color_t accent) {
    if (label && *label) add_wrapped_label(label, COL_DIM);
    lv_obj_t *bar = lv_bar_create(s_card);
    lv_obj_set_width(bar, lv_pct(100));
    lv_obj_set_height(bar, 12);
    lv_bar_set_range(bar, 0, 100);
    int pct = (int)(value * 100.0);
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    lv_bar_set_value(bar, pct, LV_ANIM_OFF);
    lv_obj_set_style_bg_color(bar, accent, LV_PART_INDICATOR);
}

// Rounded colour badge.
static void add_status_pill(const char *text, lv_color_t color) {
    lv_obj_t *pill = lv_obj_create(s_card);
    lv_obj_set_size(pill, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
    lv_obj_set_style_bg_color(pill, color, 0);
    lv_obj_set_style_radius(pill, 14, 0);
    lv_obj_set_style_pad_all(pill, 6, 0);
    lv_obj_set_style_border_width(pill, 0, 0);
    lv_obj_remove_flag(pill, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t *l = lv_label_create(pill);
    lv_label_set_text(l, text ? text : "");
    lv_obj_set_style_text_color(l, COL_TITLE, 0);
}

// ── op renderers ────────────────────────────────────────────────────
static void render_card(const cJSON *card) {
    lv_color_t accent = parse_hex(card, "accent", COL_ACCENT);
    lv_obj_clean(s_card);
    lv_obj_remove_flag(s_card, LV_OBJ_FLAG_HIDDEN);

    add_title(card, accent);

    const cJSON *subtitle = cJSON_GetObjectItem(card, "subtitle");
    if (cJSON_IsString(subtitle)) add_wrapped_label(subtitle->valuestring, COL_DIM);

    const cJSON *rows = cJSON_GetObjectItem(card, "rows");
    if (cJSON_IsArray(rows)) {
        const cJSON *it;
        cJSON_ArrayForEach(it, rows) {
            if (cJSON_IsString(it)) add_wrapped_label(it->valuestring, COL_BODY);
        }
    }

    const cJSON *fields = cJSON_GetObjectItem(card, "fields");
    if (cJSON_IsArray(fields)) {
        const cJSON *f;
        cJSON_ArrayForEach(f, fields) {
            const cJSON *l = cJSON_GetObjectItem(f, "label");
            const cJSON *v = cJSON_GetObjectItem(f, "value");
            add_field(cJSON_IsString(l) ? l->valuestring : "",
                      cJSON_IsString(v) ? v->valuestring : "");
        }
    }

    const cJSON *status = cJSON_GetObjectItem(card, "status");
    if (cJSON_IsObject(status)) {
        const cJSON *st = cJSON_GetObjectItem(status, "text");
        add_status_pill(cJSON_IsString(st) ? st->valuestring : "",
                        parse_hex(status, "color", lv_color_hex(0x16a34a)));
    }

    // single meter {label,value} and/or meters[]
    const cJSON *meter = cJSON_GetObjectItem(card, "meter");
    if (cJSON_IsObject(meter)) {
        const cJSON *v = cJSON_GetObjectItem(meter, "value");
        const cJSON *l = cJSON_GetObjectItem(meter, "label");
        if (cJSON_IsNumber(v)) add_meter(cJSON_IsString(l) ? l->valuestring : "", v->valuedouble, accent);
    }
    const cJSON *meters = cJSON_GetObjectItem(card, "meters");
    if (cJSON_IsArray(meters)) {
        const cJSON *m;
        cJSON_ArrayForEach(m, meters) {
            const cJSON *v = cJSON_GetObjectItem(m, "value");
            const cJSON *l = cJSON_GetObjectItem(m, "label");
            if (cJSON_IsNumber(v)) add_meter(cJSON_IsString(l) ? l->valuestring : "", v->valuedouble, accent);
        }
    }
}

static void render_text(const cJSON *card) {
    lv_color_t accent = parse_hex(card, "accent", COL_ACCENT);
    lv_obj_clean(s_card);
    lv_obj_remove_flag(s_card, LV_OBJ_FLAG_HIDDEN);
    add_title(card, accent);
    const cJSON *body = cJSON_GetObjectItem(card, "body");
    if (cJSON_IsString(body)) add_wrapped_label(body->valuestring, lv_color_hex(0xe5e7eb));
}

static void toast_hide_cb(lv_timer_t *t) {
    (void)t;
    if (s_toast) lv_obj_add_flag(s_toast, LV_OBJ_FLAG_HIDDEN);
}

static void render_toast(const cJSON *card) {
    const cJSON *msg = cJSON_GetObjectItem(card, "message");
    const cJSON *ttl = cJSON_GetObjectItem(card, "ttl_ms");
    lv_label_set_text(s_toast_label, cJSON_IsString(msg) ? msg->valuestring : "");
    lv_obj_set_style_bg_color(s_toast, parse_hex(card, "accent", lv_color_hex(0x334155)), 0);
    lv_obj_remove_flag(s_toast, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(s_toast);
    int ttl_ms = cJSON_IsNumber(ttl) ? (int)ttl->valuedouble : 3000;
    if (ttl_ms < 500) ttl_ms = 3000;
    lv_timer_t *t = lv_timer_create(toast_hide_cb, ttl_ms, NULL);
    lv_timer_set_repeat_count(t, 1);  // one-shot: auto-deletes after firing
}

// ── public API ──────────────────────────────────────────────────────
// Hide the image layer so a card can take the card area back.
static void hide_image_layer(void) {
    if (s_image) lv_obj_add_flag(s_image, LV_OBJ_FLAG_HIDDEN);
}

// Render the card. Invoked via lv_async_call so it runs in the LVGL task context
// (where the display lock is already held) — never from the WebSocket task.
static void render_async_cb(void *param) {
    char *json = (char *)param;
    cJSON *card = cJSON_Parse(json);
    cJSON_free(json);
    if (!card) return;
    const cJSON *op = cJSON_GetObjectItem(card, "op");
    const char *ops = cJSON_IsString(op) ? op->valuestring : "card";
    ESP_LOGI(TAG, "card render op=%s", ops);
    if (strcmp(ops, "clear") == 0) {
        hide_image_layer();
        lv_obj_clean(s_card);
        lv_obj_add_flag(s_card, LV_OBJ_FLAG_HIDDEN);
    } else if (strcmp(ops, "toast") == 0) {
        render_toast(card);  // overlay — may float over an image
    } else if (strcmp(ops, "text") == 0) {
        hide_image_layer();
        render_text(card);
    } else {
        hide_image_layer();
        render_card(card);
    }
    cJSON_Delete(card);
}

// Swap in the staged PNG and display it in the card area. Runs in the LVGL task
// (via lv_async_call) where the display lock is held — the lodepng decode happens
// here on first draw, off the WebSocket task.
static void show_image_cb(void *param) {
    (void)param;
    if (!s_image || !s_img_next) return;
    if (s_img_buf) free(s_img_buf);  // previous image's bytes — no longer drawn
    s_img_buf = s_img_next;
    s_img_len = s_img_next_len;
    s_img_next = NULL;

    // Read the real dimensions from the PNG IHDR: width @ byte 16, height @ byte 20
    // (big-endian). Giving LVGL the exact size avoids mis-layout / partial draws.
    uint32_t w = 0, h = 0;
    if (s_img_len > 24 && s_img_buf[1] == 'P' && s_img_buf[2] == 'N' && s_img_buf[3] == 'G') {
        w = ((uint32_t)s_img_buf[16] << 24) | ((uint32_t)s_img_buf[17] << 16) |
            ((uint32_t)s_img_buf[18] << 8) | s_img_buf[19];
        h = ((uint32_t)s_img_buf[20] << 24) | ((uint32_t)s_img_buf[21] << 16) |
            ((uint32_t)s_img_buf[22] << 8) | s_img_buf[23];
    }

    // Alternate descriptors so the src pointer always differs — otherwise
    // lv_image_set_src may treat it as unchanged and skip the redraw.
    s_img_slot ^= 1;
    lv_image_dsc_t *d = &s_img_dsc[s_img_slot];
    memset(d, 0, sizeof(*d));
    d->header.magic = LV_IMAGE_HEADER_MAGIC;
    d->header.cf = LV_COLOR_FORMAT_RAW;  // encoded PNG; the lodepng decoder handles it
    d->header.w = w;
    d->header.h = h;
    d->data = s_img_buf;
    d->data_size = s_img_len;
    lv_image_set_src(s_image, d);

    if (w && h) {
        lv_obj_set_size(s_image, w, h);
        lv_obj_align(s_image, LV_ALIGN_TOP_MID, 0, 36);
    }
    lv_obj_remove_flag(s_image, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(s_image);                         // on top of the card
    if (s_card) lv_obj_add_flag(s_card, LV_OBJ_FLAG_HIDDEN);  // image replaces the card
    ESP_LOGI(TAG, "image displayed %ux%u (%u B png)", (unsigned)w, (unsigned)h,
             (unsigned)s_img_len);
}

void ui_show_image(const uint8_t *png, size_t len) {
    if (!s_image || !png || !len) return;
    uint8_t *buf = heap_caps_malloc(len, MALLOC_CAP_SPIRAM);
    if (!buf) {
        ESP_LOGE(TAG, "image: no PSRAM for %u bytes", (unsigned)len);
        return;
    }
    memcpy(buf, png, len);
    if (!bsp_display_lock(1000)) {
        free(buf);
        ESP_LOGW(TAG, "image: could not schedule (lock)");
        return;
    }
    if (s_img_next) free(s_img_next);  // discard a superseded staged image
    s_img_next = buf;
    s_img_next_len = len;
    lv_async_call(show_image_cb, NULL);
    bsp_display_unlock();
}

void ui_show_card(const cJSON *card) {
    if (!s_card || !card) return;
    // Building a card touches many LVGL objects. Doing that directly from the
    // WebSocket task (while audio is streaming) fought the display lock and dropped
    // silently under load. Copy the card and schedule the render into the LVGL task.
    char *json = cJSON_PrintUnformatted(card);
    if (!json) return;
    if (bsp_display_lock(1000)) {
        lv_async_call(render_async_cb, json);  // runs later, in LVGL context
        bsp_display_unlock();
    } else {
        ESP_LOGW(TAG, "card: could not schedule render (lock)");
        cJSON_free(json);
    }
}

void ui_set_status(const char *text) {
    if (!s_status) return;
    if (bsp_display_lock(200)) {
        lv_label_set_text(s_status, text);
        bsp_display_unlock();
    }
}

void ui_reset_talk(void) {
    if (!s_talk_label) return;
    if (bsp_display_lock(200)) {
        lv_label_set_text(s_talk_label, "Hold to talk");
        bsp_display_unlock();
    }
}

// Push-to-talk (runs inside the LVGL event context — lock already held).
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

// Refresh the corner battery indicator (runs in the LVGL task via lv_timer, so
// lv_label_set_text is safe; the I²C read briefly blocks the LVGL task ~10 s apart).
static void battery_timer_cb(lv_timer_t *t) {
    (void)t;
    if (!s_battery) return;
    int pct = power_battery_percent();
    if (pct < 0) {  // gauge not ready / no PMIC
        lv_label_set_text(s_battery, LV_SYMBOL_BATTERY_EMPTY);
        return;
    }
    const char *glyph;
    if (power_is_charging())   glyph = LV_SYMBOL_CHARGE;
    else if (pct >= 90)        glyph = LV_SYMBOL_BATTERY_FULL;
    else if (pct >= 65)        glyph = LV_SYMBOL_BATTERY_3;
    else if (pct >= 40)        glyph = LV_SYMBOL_BATTERY_2;
    else if (pct >= 15)        glyph = LV_SYMBOL_BATTERY_1;
    else                       glyph = LV_SYMBOL_BATTERY_EMPTY;
    char buf[24];
    snprintf(buf, sizeof(buf), "%s %d%%", glyph, pct);
    lv_label_set_text(s_battery, buf);
}

// Boot self-test card: shows the device is up and demonstrates the renderer.
static void show_ready_card(void) {
    cJSON *c = cJSON_CreateObject();
    cJSON_AddStringToObject(c, "op", "card");
    cJSON_AddStringToObject(c, "title", "Device Ready");
    cJSON_AddStringToObject(c, "icon", "check");
    cJSON_AddStringToObject(c, "accent", "#22c55e");
    cJSON *fields = cJSON_AddArrayToObject(c, "fields");
    cJSON *f1 = cJSON_CreateObject();
    cJSON_AddStringToObject(f1, "label", "Voice");
    cJSON_AddStringToObject(f1, "value", "push to talk");
    cJSON_AddItemToArray(fields, f1);
    cJSON *f2 = cJSON_CreateObject();
    cJSON_AddStringToObject(f2, "label", "Screen");
    cJSON_AddStringToObject(f2, "value", "cards on");
    cJSON_AddItemToArray(fields, f2);
    cJSON *meters = cJSON_AddArrayToObject(c, "meters");
    cJSON *m1 = cJSON_CreateObject();
    cJSON_AddStringToObject(m1, "label", "Signal");
    cJSON_AddNumberToObject(m1, "value", 0.6);
    cJSON_AddItemToArray(meters, m1);
    ui_show_card(c);
    cJSON_Delete(c);
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
    lv_obj_set_style_text_color(s_status, COL_DIM, 0);
    lv_obj_align(s_status, LV_ALIGN_TOP_MID, 0, 8);

    // Small battery indicator, top-right corner (kept at 14px so it stays subtle).
    s_battery = lv_label_create(scr);
    lv_label_set_text(s_battery, LV_SYMBOL_BATTERY_EMPTY);
    lv_obj_set_style_text_font(s_battery, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(s_battery, COL_DIM, 0);
    lv_obj_align(s_battery, LV_ALIGN_TOP_RIGHT, -8, 8);

    // Card panel: flex column, rebuilt per card, scrolls if content overflows.
    s_card = lv_obj_create(scr);
    lv_obj_set_size(s_card, BSP_LCD_H_RES - 24, 280);
    lv_obj_align(s_card, LV_ALIGN_TOP_MID, 0, 36);
    lv_obj_set_style_bg_color(s_card, lv_color_hex(0x111827), 0);
    lv_obj_set_style_radius(s_card, 12, 0);
    lv_obj_set_style_border_width(s_card, 0, 0);
    lv_obj_set_style_pad_all(s_card, 12, 0);
    lv_obj_set_style_pad_row(s_card, 6, 0);
    lv_obj_set_flex_flow(s_card, LV_FLEX_FLOW_COLUMN);

    // Transient toast overlay (hidden until used).
    s_toast = lv_obj_create(scr);
    lv_obj_set_width(s_toast, BSP_LCD_H_RES - 40);
    lv_obj_set_height(s_toast, LV_SIZE_CONTENT);
    lv_obj_align(s_toast, LV_ALIGN_TOP_MID, 0, 4);
    lv_obj_set_style_bg_color(s_toast, lv_color_hex(0x334155), 0);
    lv_obj_set_style_radius(s_toast, 10, 0);
    lv_obj_set_style_pad_all(s_toast, 10, 0);
    lv_obj_set_style_border_width(s_toast, 0, 0);
    lv_obj_remove_flag(s_toast, LV_OBJ_FLAG_SCROLLABLE);
    s_toast_label = lv_label_create(s_toast);
    lv_label_set_long_mode(s_toast_label, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(s_toast_label, lv_pct(100));
    lv_obj_set_style_text_color(s_toast_label, COL_TITLE, 0);
    lv_obj_add_flag(s_toast, LV_OBJ_FLAG_HIDDEN);

    // Hold-to-talk control.
    s_talk = lv_obj_create(scr);
    lv_obj_set_size(s_talk, BSP_LCD_H_RES - 40, 92);
    lv_obj_align(s_talk, LV_ALIGN_BOTTOM_MID, 0, -16);
    lv_obj_set_style_bg_color(s_talk, lv_color_hex(0x2563eb), 0);
    lv_obj_set_style_radius(s_talk, 16, 0);
    lv_obj_add_flag(s_talk, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(s_talk, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(s_talk, talk_cb, LV_EVENT_PRESSED, NULL);
    lv_obj_add_event_cb(s_talk, talk_cb, LV_EVENT_RELEASED, NULL);
    s_talk_label = lv_label_create(s_talk);
    lv_label_set_text(s_talk_label, "Hold to talk");
    lv_obj_set_style_text_color(s_talk_label, COL_TITLE, 0);
    lv_obj_center(s_talk_label);

    // Image layer over the card area (hidden until a server-rendered image arrives).
    // 352 wide (multiple of 32 px → 64-byte rows) so flush strips are cache-line
    // aligned for PSRAM DMA. show_image_cb resizes to the PNG's actual dimensions.
    s_image = lv_image_create(scr);
    lv_obj_set_size(s_image, 352, 280);
    lv_obj_align(s_image, LV_ALIGN_TOP_MID, 0, 36);
    lv_obj_add_flag(s_image, LV_OBJ_FLAG_HIDDEN);

    // Battery: refresh now and every 10 s.
    lv_timer_t *bat = lv_timer_create(battery_timer_cb, 10000, NULL);
    battery_timer_cb(bat);

    bsp_display_unlock();

    show_ready_card();
}
