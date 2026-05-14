# Threat Model — Phantom ESP32 Anti-Reverse Engineering

## 1. Attack Vectors và Mức độ bảo vệ

| Attack Vector | Mô tả | Biện pháp bảo vệ | Hiệu quả |
|---|---|---|---|
| Flash dump (esptool) | Đọc toàn bộ flash qua UART | Flash Encryption (AES-XTS) | ★★★★★ |
| JTAG debugging | Kết nối debugger, đọc RAM/registers | CONFIG_SECURE_DISABLE_JTAG=y | ★★★★★ |
| UART serial monitor | Đọc Serial.printf output | PHANTOM_DEBUG=0 + -g0 | ★★★★☆ |
| SD card extraction | Lấy thẻ SD, đọc file .bin | AES-GCM 256-bit encryption | ★★★★★ |
| Binary analysis (Ghidra/IDA) | Phân tích ELF/binary | Strip symbols + -O2 + -g0 | ★★★☆☆ |
| Firmware replacement | Flash firmware giả | Secure Boot v2 (RSA-signed) | ★★★★★ |
| WiFi sniffing | Bắt gói tin WiFi | Dữ liệu audio đã mã hóa trước khi truyền | ★★★★☆ |
| NVS key extraction | Đọc NVS partition | NVS Encryption (tied to eFuse) | ★★★★★ |
| Side-channel (power analysis) | Đo điện năng khi mã hóa | Không có biện pháp (hardware limitation) | ★☆☆☆☆ |
| Supply chain attack | Thay chip/module | Secure Boot + Flash Encryption | ★★★★☆ |

---

## 2. Chi tiết từng lớp bảo vệ

### 2.1 Flash Encryption (AES-XTS)
- **Cơ chế:** ESP32 tự tạo key 256-bit, burn vào eFuse (không thể đọc ra). Toàn bộ flash được mã hóa tại chỗ khi boot lần đầu.
- **Kết quả:** `esptool.py read_flash` trả về dữ liệu mã hóa — không thể phân tích.
- **Giới hạn:** Key trong eFuse có thể bị đọc bằng thiết bị chuyên dụng (fault injection, EM probing) — rủi ro rất thấp với attacker thông thường.

### 2.2 Secure Boot v2 (RSA-3072)
- **Cơ chế:** Bootloader verify chữ ký RSA của app trước khi chạy. Firmware không ký bị từ chối.
- **Kết quả:** Không thể flash firmware giả/modified.
- **Giới hạn:** Nếu mất private key, không thể cập nhật firmware.

### 2.3 JTAG Disable
- **Cơ chế:** eFuse `JTAG_DISABLE` burn vĩnh viễn.
- **Kết quả:** OpenOCD/J-Link không thể kết nối.
- **Giới hạn:** Không bảo vệ được nếu attacker có chip decapper.

### 2.4 AES-GCM 256-bit cho file audio
- **Cơ chế:** Mỗi file .bin = PHGCM1 + IV(12B) + ciphertext + TAG(16B). IV ngẫu nhiên mỗi lần.
- **Key mới:** `5858a9657a5b31a037d40f28ca2982627dd31e853963a226d690b96f611b1681`
- **Kết quả:** Lấy thẻ SD không đọc được audio.
- **Giới hạn:** Key vẫn compile-time fallback nếu NVS chưa được set. Cần chạy `phantomStoreKeyInNVS()` một lần.

### 2.5 Debug output disable
- **Cơ chế:** `-DPHANTOM_DEBUG=0` → tất cả DBG_PRINTF/DBG_PRINTLN compile thành no-op.
- **Kết quả:** Serial monitor không thấy thông tin nội bộ (IP, sync status, file paths).

### 2.6 Symbol stripping + compiler hardening
- **Cơ chế:** `xtensa-esp32-elf-strip --strip-all` + `-O2 -g0 -fvisibility=hidden`
- **Kết quả:** Ghidra/IDA không thấy tên hàm, biến. Decompile khó hơn đáng kể.
- **Giới hạn:** String literals vẫn có thể xuất hiện trong binary (WiFi SSID, endpoint paths).

---

## 3. Test Plan — Kiểm tra từng lớp bảo vệ

### Test 1: Flash Encryption
```bash
# Sau khi enable Flash Encryption, thử đọc flash:
esptool.py --port COM15 --baud 115200 read_flash 0x0 0x400000 dump.bin

# Kiểm tra: dump.bin phải là dữ liệu ngẫu nhiên, không có chuỗi rõ ràng
strings dump.bin | grep -iE "phantom|wifi|password|192\.168"
# Kết quả mong đợi: không có output
```

### Test 2: JTAG Disable
```bash
# Thử kết nối OpenOCD:
openocd -f interface/ftdi/esp32_devkitj_v1.cfg -f target/esp32.cfg
# Kết quả mong đợi: "Error: JTAG scan chain interrogation failed"
```

### Test 3: Binary analysis
```bash
# Chạy strings trên firmware.bin:
strings esp32_server/.pio/build/nodemcu-32s/firmware.bin | grep -iE "serial|debug|printf|SD.*init"
# Kết quả mong đợi: không có debug strings

# Kiểm tra symbol table:
xtensa-esp32-elf-nm esp32_server/.pio/build/nodemcu-32s/firmware.elf 2>&1
# Kết quả mong đợi: "no symbols"
```

### Test 4: SD card extraction
```bash
# Python: thử decrypt file .bin không có key
python3 -c "
import struct
with open('phantom_xxx.bin', 'rb') as f:
    data = f.read()
magic = data[:6]
print('Magic:', magic)  # Phải là b'PHGCM1'
# Không có key -> không decrypt được
"
```

### Test 5: Serial monitor
```bash
# Kết nối serial monitor sau khi build với PHANTOM_DEBUG=0:
# Kết quả mong đợi: chỉ thấy output từ Serial.begin() — không có thông tin nội bộ
```

### Test 6: Firmware integrity (boot check)
```bash
# Xem serial output khi boot (development mode):
# Kết quả mong đợi:
# [Security] App SHA-256: <hash>
# [Security] Watchdog configured: 30s timeout
```

---

## 4. Rủi ro còn lại

| Rủi ro | Mức độ | Lý do không thể bảo vệ hoàn toàn |
|---|---|---|
| Side-channel (power/EM) | Thấp | Cần thiết bị chuyên dụng >$10k |
| Chip decapping | Rất thấp | Cần lab chuyên nghiệp |
| WiFi SSID/password lộ trong binary | Trung bình | String literals không mã hóa |
| AES key trong compile-time fallback | Trung bình | Cần chạy phantomStoreKeyInNVS() |
| Mất Secure Boot signing key | Cao | Không thể update firmware |

---

## 5. Checklist trước khi deploy production

- [ ] Tạo `secure_boot_signing_key.pem` và backup an toàn
- [ ] Thay AES_GCM_KEY từ demo key sang key ngẫu nhiên ✅ (đã làm)
- [ ] Chạy `phantomStoreKeyInNVS()` một lần để lưu key vào NVS
- [ ] Build với `-DPHANTOM_DEBUG=0` ✅ (đã cấu hình)
- [ ] Build với `-O2 -g0 -DNDEBUG` ✅ (đã cấu hình)
- [ ] Strip symbols với `strip_symbols.py` ✅ (đã cấu hình)
- [ ] Enable Flash Encryption (development mode trước)
- [ ] Enable Secure Boot v2
- [ ] Test firmware hoạt động đúng trong development mode
- [ ] Chạy Test 1-6 ở trên
- [ ] Đổi sang RELEASE mode (KHÔNG THỂ ĐẢO NGƯỢC)
- [ ] Flash production firmware lần cuối
- [ ] Verify esptool không đọc được flash

---

## 6. Key hiện tại (lưu ở nơi an toàn)

```
AES-GCM 256-bit key (hex):
5858a9657a5b31a037d40f28ca2982627dd31e853963a226d690b96f611b1681
```

Dùng key này trong `de_audio.py` để decrypt file .bin.
