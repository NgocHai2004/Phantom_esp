# Project

## API-CONTRACT

| Surface | Method | Endpoint | Request | Response fields used | Fallback behavior | Notes |
|---|---|---|---|---|---|---|
| Phantom-1 status (`192.168.4.1`) | `GET` | `http://192.168.4.1/status` | None | `public_key` (+ battery fields if available) | Battery UI shows `--` when fields absent | Firmware không thay đổi trong pipeline PH2-BATTERY |
| Phantom-2 status (`192.168.5.1`) | `GET` | `http://192.168.5.1/status` | None | `battery_voltage` (float V, 2dp), `battery_voltage_raw` (int 0..4095), `battery_percent` (int 0..100), `public_key` | UI fallback `--` nếu thiếu | ADC GPIO34, divider ×2.0, dải 3.0..4.2V; trung bình 16 mẫu |
| UI rendering both phantoms | n/a | n/a | n/a | `battery_percent` ưu tiên → `battery_voltage_norm` → `battery_voltage`/`battery_pin_voltage`/`battery_voltage_raw` | `--` khi không parse được | Icon thresholds: `>80` green 🔋, `20<v<=80` white 🔋, `<=20` red 🔋 |

## Pipeline: PH2-BATTERY (active)
- Goal: Phantom-2 hiển thị pin giống Phantom-1 (cả firmware lẫn UI).
- Hardware: ADC GPIO34 + voltage divider 2 điện trở (R1=R2 → hệ số 2.0), Vref=3.3V, ADC 12-bit (0..4095), dải pin 3.0V..4.2V.
- Firmware contract (cả `esp32_server/src/main.cpp` và `esp32_client/src/main.cpp`):
  - Thêm `BATTERY_ADC_PIN = 34`, `BATTERY_DIVIDER = 2.0f`.
  - Hàm `readBatteryVoltage()` trung bình 16 mẫu `analogRead(34)` → volt thực × 2.0.
  - Hàm `batteryPercent(v)` map 3.0..4.2V → 0..100 (clamp).
  - `handleStatus()` thêm 3 field: `battery_voltage` (float, 2 chữ số), `battery_voltage_raw` (int raw), `battery_percent` (int).
- UI contract (`en_de.py`):
  - Phantom-2 dùng chung `_battery_icon_and_color()` + `_battery_percent_from_status()` như Phantom-1.
  - Hiển thị icon pin + text `BAT xx%` cho Phantom-2 tại cùng vị trí Phantom-1.
  - Fallback `--` khi không parse được.
- Flow: BE Dev (firmware 2 file + en_de.py UI) → Integration (build firmware, syntax check py) → QC.

## BE Dev Task Notes

### PH2-BATTERY (current)
- **Scope chốt với user**: KHÔNG động vào Phantom-1 firmware. Chỉ thêm ADC cho Phantom-2 và làm UI Phantom-2 hiển thị y hệt Phantom-1.
- **Firmware Phantom-2** ([`esp32_client/src/main.cpp`](esp32_client/src/main.cpp:1)):
  - Constants `BATTERY_ADC_PIN=34`, `BATTERY_DIVIDER=2.0f`, `BATTERY_V_MIN=3.0f`, `BATTERY_V_MAX=4.2f` ([line 65-68](esp32_client/src/main.cpp:65)).
  - 3 helper functions [`readBatteryVoltage()`](esp32_client/src/main.cpp:617), [`batteryPercent()`](esp32_client/src/main.cpp:625), [`readBatteryRaw()`](esp32_client/src/main.cpp:630). Trung bình 16 mẫu, delay 200µs giữa các sample.
  - [`handleStatus()`](esp32_client/src/main.cpp:637) chèn 3 field battery TRƯỚC `}` cuối JSON, sau `builtin_wav_size`.
  - [`setup()`](esp32_client/src/main.cpp:1133) gọi `analogReadResolution(12)` + `analogSetPinAttenuation(BATTERY_ADC_PIN, ADC_11db)` ngay sau `pinMode` và TRƯỚC `WiFi.mode()`.
- **UI** ([`en_de.py`](en_de.py:1)):
  - [`EncryptPage._on_scan_result()`](en_de.py:1201) thêm nhánh `elif nm == "Phantom-2":` y hệt Phantom-1 — set `_conn_lbl` thành `Phantom-2  ONLINE  ·  BAT xx%` và `_conn_batt_icon` với màu theo threshold. Nhánh Phantom-1 giữ nguyên.
- **Hardware giả định**: GPIO34 chưa dùng (firmware chỉ dùng GPIO0=BOOT, GPIO2=LED → an toàn). Voltage divider 100kΩ/100kΩ giữa V_BAT và GND, mid-point → GPIO34.
- **Bỏ qua**: Phantom-1 firmware (per user). Build firmware (Integration sẽ làm bằng PlatformIO).
- **Syntax check**: `python -m py_compile en_de.py` → exit `0`.
- **C/C++ IntelliSense errors về `Arduino.h`** trong VSCode chỉ là cảnh báo include path — PlatformIO build sẽ resolve chuẩn.
- **Next**: Integration build `esp32_client` (`pio run -d esp32_client`), flash Phantom-2, verify `curl http://192.168.5.1/status` chứa 3 field battery, mở `en_de.py` connect Phantom-2 → kiểm tra icon + BAT hiển thị.

## Integration Log (PH2-BATTERY)

**Verdict: PASS** — 2026-04-21

| # | Check | Result |
|---|---|---|
| 1 | `pio run -d esp32_client` | ✅ SUCCESS (37.5s) — RAM 13.9% (45424 B), Flash 42.0% (881001 B / 2 MB) |
| 2 | `pio run -d esp32_server` (sanity, no code change) | ✅ SUCCESS (11.0s) — RAM 13.8% (45216 B), Flash 41.7% (874833 B) — không regression |
| 3 | `python -m py_compile en_de.py` | ✅ exit 0 (`PY_OK`) |
| 4 | Static JSON check [`handleStatus()`](esp32_client/src/main.cpp:645) | ✅ 3 field chèn GIỮA `builtin_wav_size` ([line 662](esp32_client/src/main.cpp:662)) và `"}"` ([line 665](esp32_client/src/main.cpp:665)). Leading `,` duy nhất, không double-comma, không `}}` thừa. Format `String(_bv,2)` đúng 2 chữ số thập phân |
| 5 | UI branch [`en_de.py:1232`](en_de.py:1232) | ✅ `elif nm == "Phantom-2":` dùng cùng `_battery_percent_from_status` ([line 1218](en_de.py:1218)) + `_battery_icon_and_color` ([line 1225](en_de.py:1225)) với Phantom-1. Text `BAT {batt_text}`, icon + color nhất quán |

**Build size delta Phantom-2** (baseline trước khi có battery logic không đo được — chỉ có snapshot sau): Flash 881 KB / 2 MB, RAM 44 KB / 320 KB. So với Phantom-1 (874 KB) chênh ~6 KB (String ops + 3 helpers) — hợp lý.

**Warnings đáng chú ý**: không có. Build log sạch, không có `-Wunused` hoặc `-Wconversion` warnings cho code mới.

**Không verify được** (ngoài scope, cần hardware):
- Endpoint thật `GET http://192.168.5.1/status` (cần flash + WiFi AP live).
- Giá trị ADC thực từ GPIO34 (cần voltage divider vật lý).
- UI Tk render Phantom-2 với icon pin (cần hardware + env Python có customtkinter).

**Signal**: `integration-verified` → vision-parser (có thay đổi UI `en_de.py`).
