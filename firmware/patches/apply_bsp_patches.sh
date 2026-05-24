#!/usr/bin/env bash
# Re-applies our local hardening to the Waveshare BSP managed component.
# managed_components/ is git-ignored and gets re-fetched by `idf.py reconfigure`,
# which reverts this patch — so run this script again after that.
#
#   bash firmware/patches/apply_bsp_patches.sh
#
# What it does: makes FT3168 touch init NON-FATAL with a retry. The stock BSP
# hard-aborts (ESP_ERROR_CHECK) if the touch I2C probe fails at boot, which on
# this board is intermittent and causes boot-loops. Idempotent.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BSP="$HERE/../managed_components/waveshare__esp32_s3_touch_amoled_1_8/esp32_s3_touch_amoled_1_8.c"
[ -f "$BSP" ] || { echo "BSP not found — run 'idf.py reconfigure' first: $BSP"; exit 1; }

python3 - "$BSP" <<'PY'
import sys
path = sys.argv[1]
s = open(path).read()
if "LOCAL PATCH (claude-esp)" in s:
    print("BSP already patched — nothing to do"); sys.exit(0)

old1 = '''static lv_indev_t *bsp_display_indev_init(lv_display_t *disp)
{
    BSP_ERROR_CHECK_RETURN_NULL(bsp_touch_new(NULL, &tp));
    assert(tp);

    /* Add touch input (for selected screen) */'''

new1 = '''static lv_indev_t *bsp_display_indev_init(lv_display_t *disp)
{
    /* LOCAL PATCH (claude-esp): touch is optional. The FT3168 I2C probe is
     * intermittent at boot on this board; retry a few times, but never abort the
     * whole app — boot with a working screen even if touch ultimately fails. */
    esp_err_t terr = ESP_FAIL;
    for (int i = 0; i < 6 && terr != ESP_OK; i++) {
        terr = bsp_touch_new(NULL, &tp);
        if (terr != ESP_OK) {
            ESP_LOGW(TAG, "touch init attempt %d failed (0x%x), retrying", i + 1, terr);
            vTaskDelay(pdMS_TO_TICKS(120));
        }
    }
    if (terr != ESP_OK || !tp) {
        ESP_LOGE(TAG, "touch init failed after retries — continuing without touch");
        return NULL;
    }

    /* Add touch input (for selected screen) */'''

old2 = '''    BSP_NULL_CHECK(disp = bsp_display_lcd_init(cfg), NULL);

    BSP_NULL_CHECK(disp_indev = bsp_display_indev_init(disp), NULL);

    BSP_ERROR_CHECK_RETURN_NULL(bsp_display_brightness_init());'''

new2 = '''    BSP_NULL_CHECK(disp = bsp_display_lcd_init(cfg), NULL);

    /* LOCAL PATCH (claude-esp): touch optional — don't fail boot if it's NULL. */
    disp_indev = bsp_display_indev_init(disp);

    BSP_ERROR_CHECK_RETURN_NULL(bsp_display_brightness_init());'''

for old, new, label in [(old1, new1, "indev_init"), (old2, new2, "start_with_config")]:
    if old not in s:
        print(f"ERROR: could not find block to patch ({label}) — BSP version drift?")
        sys.exit(2)
    s = s.replace(old, new, 1)

open(path, "w").write(s)
print("BSP patched OK (touch init is now non-fatal with retry)")
PY
