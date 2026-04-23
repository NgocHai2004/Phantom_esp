# Code Map

## Backend

### `en_de.py`
- [`scan_phantoms()`](en_de.py:113): probes known Phantom IPs via [`GET /status`](en_de.py:117) and stores full status JSON for UI/state decisions.
- [`_battery_percent_from_status()`](en_de.py:130): robust battery parser with priority order:
  1. `battery_percent`
  2. `battery_voltage_norm` (supports `0..1` and `0..100` formats)
  3. voltage fallback from `battery_voltage` / `battery_pin_voltage` / `battery_voltage_raw` mapped to `3.0V..4.2V`.
- [`EncryptPage._poll_detect()`](en_de.py:1172): refresh trigger now tracks `(ip, battery_pct)` so battery changes are reflected in status UI.
- [`_battery_icon_and_color()`](en_de.py:203): battery icon/color mapper used for Phantom-1 display:
  - `battery > 80` → green `🔋`
  - `20 < battery <= 80` → white `🔋`
  - `battery <= 20` → red `🔋`
  - missing battery (`None`) → placeholder tint (`C_TEXT3`) with `--` text fallback.
- [`EncryptPage._on_scan_result()`](en_de.py:1201): renders identical battery UI (icon + `BAT xx%` in top conn label, `Battery: 🔋 xx%` in sidebar) for **Phantom-1 AND Phantom-2**; other Phantoms fall back to text-only `--`. Phantom-1 logic untouched.

### Status payload fields consumed
- `public_key`
- `battery_percent`
- `battery_voltage_norm`
- `battery_voltage`
- `battery_pin_voltage`
- `battery_voltage_raw`

## Firmware / ESP32

### `esp32_client/src/main.cpp` (Phantom-2, AP `192.168.5.1`)
- Constants [`BATTERY_ADC_PIN=34`](esp32_client/src/main.cpp:65), [`BATTERY_DIVIDER=2.0f`](esp32_client/src/main.cpp:66), [`BATTERY_V_MIN=3.0f`](esp32_client/src/main.cpp:67), [`BATTERY_V_MAX=4.2f`](esp32_client/src/main.cpp:68).
- [`readBatteryVoltage()`](esp32_client/src/main.cpp:617): trung bình 16 mẫu `analogRead(34)` → V × 2.0.
- [`batteryPercent()`](esp32_client/src/main.cpp:625): map 3.0..4.2V → 0..100 (clamp).
- [`readBatteryRaw()`](esp32_client/src/main.cpp:630): raw ADC trung bình 16 mẫu.
- [`handleStatus()`](esp32_client/src/main.cpp:637): `GET /status` JSON nay gồm 3 field mới `battery_voltage` (float 2dp), `battery_voltage_raw` (int 0..4095), `battery_percent` (int 0..100), kèm các field cũ.
- [`setup()`](esp32_client/src/main.cpp:1133): khởi tạo `analogReadResolution(12)` + `analogSetPinAttenuation(34, ADC_11db)` TRƯỚC `WiFi.mode()`.

### `esp32_server/src/main.cpp` (Phantom-1)
- Không thay đổi trong pipeline PH2-BATTERY (giữ nguyên hành vi hiện tại; UI đã fallback `--` khi không có battery field).

### Runtime validation
- Syntax validation command: `python -m py_compile en_de.py` (exit code `0`).
