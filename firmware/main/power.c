// power implementation — minimal AXP2101 register reads over I²C.
// Ported from pet-esp. Two pieces of state for the battery indicator:
//   - battery percent (register 0xA4, single byte, 0..100, 0xFF=not ready)
//   - charging state  (register 0x01, bits[2:0] = charge state machine)
// AXP2101 datasheet rev. 1.0 §7.2 (status) and §7.10 (fuel gauge). I²C addr 0x34.

#include "power.h"

#include "bsp/esp32_s3_touch_amoled_1_8.h"  // bsp_i2c_get_handle()
#include "driver/i2c_master.h"
#include "esp_log.h"

static const char *TAG = "power";

#define AXP2101_I2C_ADDR     0x34
#define AXP2101_I2C_HZ       400000
#define REG_CHARGE_STATUS    0x01   // bits[2:0] = charge state machine
#define REG_BATT_PERCENT     0xA4   // 0..100, 0xFF when fuel gauge cold

static i2c_master_dev_handle_t s_dev;
static bool s_ok;

// Single-byte register read. 50 ms timeout — the shared bus also has touch/codec.
static bool read_reg(uint8_t reg, uint8_t *out) {
    if (!s_ok) return false;
    esp_err_t e = i2c_master_transmit_receive(s_dev, &reg, 1, out, 1, 50);
    if (e != ESP_OK) {
        ESP_LOGW(TAG, "i2c read reg 0x%02x failed: %s", reg, esp_err_to_name(e));
        return false;
    }
    return true;
}

bool power_init(void) {
    i2c_master_bus_handle_t bus = bsp_i2c_get_handle();
    if (bus == NULL) {
        ESP_LOGE(TAG, "BSP I²C bus not initialised");
        return false;
    }
    i2c_device_config_t cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address  = AXP2101_I2C_ADDR,
        .scl_speed_hz    = AXP2101_I2C_HZ,
    };
    if (i2c_master_bus_add_device(bus, &cfg, &s_dev) != ESP_OK) {
        ESP_LOGE(TAG, "add AXP2101 device failed");
        return false;
    }
    s_ok = true;
    uint8_t status;
    if (read_reg(REG_CHARGE_STATUS, &status)) {
        ESP_LOGI(TAG, "AXP2101 online (status=0x%02x)", status);
    } else {
        ESP_LOGW(TAG, "AXP2101 not responding — battery readouts will be -1");
        s_ok = false;
    }
    return s_ok;
}

int power_battery_percent(void) {
    uint8_t v;
    if (!read_reg(REG_BATT_PERCENT, &v)) return -1;
    if (v == 0xFF) return -1;   // fuel gauge not settled yet
    if (v > 100) v = 100;
    return v;
}

bool power_is_charging(void) {
    uint8_t status;
    if (!read_reg(REG_CHARGE_STATUS, &status)) return false;
    // bits[2:0]: 1=trickle 2=pre 3=CC 4=CV are "charging"; 0/5/6/7 are not.
    uint8_t phase = status & 0x07;
    return phase >= 1 && phase <= 4;
}
