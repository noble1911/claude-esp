// power — AXP2101 PMIC accessors: battery percent + charging state for the
// on-screen battery indicator. The Waveshare BSP doesn't wrap the AXP2101, so we
// talk to it directly over the shared I²C bus the BSP already brought up.
// Ported from pet-esp (firmware/components/power).

#pragma once

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

// Attach the AXP2101 onto the BSP's I²C bus. Call after bsp_display_start()
// (which initialises I²C for the touch panel). Returns true on success; false
// leaves the API as safe no-ops (a bad PMIC won't take the UI down).
bool power_init(void);

// Battery percent 0..100, or -1 if the gauge isn't ready / I²C failed. The
// AXP2101 fuel gauge needs a few seconds after boot to settle (expect -1 early).
int power_battery_percent(void);

// True while USB-C is actively charging the battery (not just "plugged in, full").
bool power_is_charging(void);

#ifdef __cplusplus
}
#endif
