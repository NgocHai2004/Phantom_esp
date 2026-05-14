# Changes — security/firmware-anti-reversing

## Mục tiêu
Loại bỏ toàn bộ thông tin nhạy cảm khỏi firmware binary, ngăn chặn reverse engineering khi thiết bị rơi vào tay người xấu.

---

## Các file thay đổi

### `esp32_server/src/phantom_security.h` *(mới)*
- Runtime XOR decode cho WiFi SSID, password, IP address, NVS namespace/key
- Boot integrity check qua `esp_partition_get_sha256()`
- NVS key storage: `phantomStoreKeyInNVS()` / `phantomLoadKeyFromNVS()`
- Watchdog config: `phantomConfigWatchdog()` / `phantomFeedWatchdog()`
- Không có Serial output — không lộ thông tin qua UART

### `esp32_client/src/phantom_security.h` *(mới)*
- Bản sao của server, dùng chung cho cả 2 node

### `esp32_server/src/main.cpp`
- Xóa `#define MY_AP_SSID "Phantom-1"` và các string WiFi hardcoded
- Thay bằng `g_ap_ssid`, `g_peer_ssid`, `g_ap_pass`, `g_peer_pass` — decode lúc runtime
- Xóa `#define MY_AP_IP_STR "192.168.4.1"` và `PEER_IP`
- Thay bằng `g_ap_ip`, `g_peer_ip` — decode lúc runtime
- Đổi JSON key `ap_ssid` → `nm`, `peer_ssid` → `pnm`
- Đổi file prefix `phantom_` → `px_`
- Thay AES key từ demo `0x00,0x11...` sang key ngẫu nhiên 256-bit
- Đổi `static const uint8_t AES_GCM_KEY` → `static uint8_t` để NVS có thể ghi đè
- Thêm `#include "phantom_security.h"`
- Thêm DBG macros — toàn bộ Serial output bị compile thành no-op khi `PHANTOM_DEBUG=0`
- Trung lập hóa syncMsg: xóa tên node "Phantom-1/2" khỏi tất cả status string

### `esp32_client/src/main.cpp`
- Áp dụng tất cả thay đổi tương tự server
- Thêm `phantomBootIntegrityCheck()`, `phantomConfigWatchdog()`, `phantomLoadKeyFromNVS()` vào `setup()`
- Đồng bộ AES key với server

### `esp32_server/platformio.ini` & `esp32_client/platformio.ini`
- `build_type = release`
- Compiler hardening: `-O2 -g0 -fvisibility=hidden -ffunction-sections -fdata-sections`
- `-fstack-protector-strong -D_FORTIFY_SOURCE=2 -fomit-frame-pointer -DNDEBUG`
- `extra_scripts = post:../strip_symbols.py`
- `-DPHANTOM_DEBUG=0`

### `strip_symbols.py` *(mới)*
- Post-build script chạy `xtensa-esp32-elf-strip --strip-all` trên firmware.elf
- Kết quả: 13MB ELF → 1.4MB (xóa 11.7MB symbol table)

### `sdkconfig.defaults` *(mới)*
- Flash Encryption (AES-XTS, development mode)
- Secure Boot v2 (RSA-3072)
- JTAG disable vĩnh viễn qua eFuse
- NVS Encryption
- Watchdog 30s
- Brownout detector

### `en_de.py`
- Cập nhật JSON key `ap_ssid` → `nm` để khớp với firmware mới

### `THREAT_MODEL.md` *(mới)*
- Bảng đánh giá 10 attack vector và mức độ bảo vệ
- Chi tiết từng lớp bảo vệ
- Test plan 6 bước
- Checklist deploy production

### `SECURITY_FLASH_GUIDE.md` *(mới)*
- Hướng dẫn từng bước enable Flash Encryption + Secure Boot trên hardware

---

## Kết quả kiểm tra

```bash
strings firmware.bin | grep -iE "Phantom|12345|192\.168|ap_ssid|peer_ssid"
# Output: không có kết quả từ application code
```

| Thông tin | Trước | Sau |
|---|---|---|
| WiFi SSID | Plaintext trong binary | XOR-encoded, decode lúc runtime |
| WiFi Password | Plaintext trong binary | XOR-encoded, decode lúc runtime |
| IP Address | Plaintext trong binary | XOR-encoded, decode lúc runtime |
| NVS namespace/key | Plaintext trong binary | XOR-encoded, decode lúc runtime |
| Symbol table | 13MB (tên hàm/biến đầy đủ) | Stripped hoàn toàn |
| Debug output | Serial.printf khắp nơi | No-op khi PHANTOM_DEBUG=0 |
| Audio files (SD) | Chưa mã hóa | AES-GCM 256-bit |
