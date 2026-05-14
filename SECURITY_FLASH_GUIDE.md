# Hướng dẫn bảo mật Flash Encryption + Secure Boot cho Phantom ESP32

> **CẢNH BÁO:** Một số bước dưới đây là KHÔNG THỂ ĐẢO NGƯỢC. Đọc kỹ trước khi thực hiện trên thiết bị thật.

---

## 1. Cài đặt công cụ

```bash
pip install esptool espsecure
```

---

## 2. Tạo khóa ký Secure Boot

```bash
# Tạo khóa RSA-3072 cho Secure Boot v2
espsecure.py generate_signing_key --version 2 secure_boot_signing_key.pem

# Lưu file này ở nơi AN TOÀN — mất khóa = không thể cập nhật firmware
# KHÔNG commit file này lên git
```

Thêm vào `.gitignore`:
```
secure_boot_signing_key.pem
secure_boot_signing_key_public.pem
```

---

## 3. Chế độ DEVELOPMENT (có thể flash lại)

Dùng cho giai đoạn phát triển. Flash Encryption và Secure Boot bật nhưng vẫn cho phép re-flash.

### sdkconfig.defaults (development):
```
CONFIG_SECURE_FLASH_ENC_ENABLED=y
CONFIG_SECURE_FLASH_ENCRYPTION_MODE_DEVELOPMENT=y
CONFIG_SECURE_BOOT=y
CONFIG_SECURE_BOOT_V2_ENABLED=y
CONFIG_SECURE_BOOT_SIGNING_KEY="secure_boot_signing_key.pem"
CONFIG_SECURE_DISABLE_JTAG=y
```

### Build và flash:
```bash
cd esp32_server
pio run --target upload
```

Lần đầu boot sau khi flash:
- ESP32 tự tạo Flash Encryption key và burn vào eFuse
- Firmware được mã hóa tại chỗ
- Các lần flash tiếp theo cần dùng `esptool.py --encrypt`

### Re-flash trong development mode:
```bash
esptool.py --port COM15 --baud 115200 \
  write_flash --encrypt \
  0x0 build/.../bootloader.bin \
  0x8000 build/.../partitions.bin \
  0x10000 build/.../firmware.bin
```

---

## 4. Chế độ PRODUCTION (KHÔNG THỂ ĐẢO NGƯỢC)

> **CẢNH BÁO TUYỆT ĐỐI:**
> - Sau khi burn production mode, KHÔNG THỂ flash lại firmware không ký
> - KHÔNG THỂ đọc flash bằng esptool
> - KHÔNG THỂ debug bằng JTAG
> - Mất khóa ký = thiết bị bị khóa vĩnh viễn

### Bước 1: Thay đổi sdkconfig.defaults:
```
# Thay dòng DEVELOPMENT bằng:
CONFIG_SECURE_FLASH_ENCRYPTION_MODE_RELEASE=y
CONFIG_SECURE_DISABLE_ROM_DL_MODE=y
```

### Bước 2: Build lại và flash lần cuối:
```bash
pio run --target upload
```

### Bước 3: Verify:
```bash
# Thử đọc flash — phải thất bại hoặc trả về dữ liệu mã hóa
esptool.py --port COM15 read_flash 0x0 0x400000 dump.bin
# Kiểm tra dump.bin — không được chứa chuỗi rõ ràng
strings dump.bin | grep -i "phantom\|wifi\|password"
```

---

## 5. Lưu trữ AES-GCM key trong NVS

Sau khi Flash Encryption bật, NVS cũng được mã hóa. Lưu key vào NVS thay vì hardcode:

```cpp
// Lần đầu setup (chạy một lần):
uint8_t myKey[32] = { /* key của bạn */ };
phantomStoreKeyInNVS(myKey, 32);

// Mỗi lần boot:
uint8_t runtimeKey[32];
if (!phantomLoadKeyFromNVS(runtimeKey, 32)) {
    // Fallback về compile-time key
    memcpy(runtimeKey, AES_GCM_KEY, 32);
}
```

---

## 6. Checklist trước khi deploy production

- [ ] Đã tạo và backup `secure_boot_signing_key.pem` ở nơi an toàn
- [ ] Đã test firmware hoạt động đúng trong development mode
- [ ] Đã thay AES_GCM_KEY từ demo key sang key ngẫu nhiên
- [ ] Đã lưu AES key vào NVS
- [ ] Đã tắt tất cả Serial.printf debug output (`-DPHANTOM_DEBUG=0`)
- [ ] Đã build với `-O2 -g0 -DNDEBUG`
- [ ] Đã chạy `strip_symbols.py` để xóa symbol table
- [ ] Đã verify firmware hoạt động đúng lần cuối
- [ ] Đã đổi sdkconfig sang RELEASE mode
- [ ] Đã flash production firmware
- [ ] Đã verify esptool không đọc được flash

---

## 7. Khóa AES-GCM hiện tại

Key hiện tại (đã thay từ demo key):
```
5858a9657a5b31a037d40f28ca298262
7dd31e853963a226d690b96f611b1681
```

Lưu key này ở nơi an toàn để decrypt file .bin trên Python GUI (`de_audio.py`).
