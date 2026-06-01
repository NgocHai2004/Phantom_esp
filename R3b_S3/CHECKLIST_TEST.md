# CHECKLIST TEST - ESP32-S3 (R3b_S3)
## Phiên bản: r3b v2.7.0.1

---

## A. PHẦN CỨNG (kiểm tra trước khi flash)

- [ ] Nối đúng module microSD:
  - SCK → GPIO12
  - MOSI → GPIO11
  - MISO → GPIO13
  - CS → GPIO10
  - VCC → 3V3, GND → GND
- [ ] LED RED nối GPIO21
- [ ] LED GREEN nối GPIO47
- [ ] LED WHITE nối GPIO48
- [ ] I2S Mic (nếu có): BCLK→GPIO4, WS→GPIO5, SD→GPIO6
- [ ] Cắm thẻ microSD đã format FAT32

---

## B. FLASH & KHỞI ĐỘNG

- [ ] Build thành công (`pio run` không lỗi)
- [ ] Upload thành công (`pio run -t upload`)
- [ ] Mở Serial Monitor (115200 baud)
- [ ] Thấy dòng `=== ESP32-S3 microSD WIFI SERVER ===`
- [ ] Thấy `[SD] OK: Mount SD thanh cong o ... Hz`
- [ ] Thấy `[WIFI] OK: Da phat WiFi AP`
- [ ] Thấy `=== READY ===`

---

## C. LED TRẠNG THÁI

- [ ] Khi khởi động: LED RED sáng (đang init)
- [ ] Sau khi init xong: LED RED tắt
- [ ] Nếu SD lỗi: LED RED nhấp nháy nhanh (300ms)
- [ ] Nếu WiFi lỗi: LED RED nhấp nháy chậm (500ms)
- [ ] Khi có thiết bị kết nối WiFi: LED GREEN sáng
- [ ] Khi không có thiết bị nào: LED GREEN tắt
- [ ] Khi upload file thành công: LED WHITE sáng 500ms rồi tắt

---

## D. WIFI ACCESS POINT

- [ ] Tìm thấy WiFi SSID: `7068616e746f6d303030303030300002`
- [ ] Kết nối được với mật khẩu: `12345678`
- [ ] Nhận IP trong dải 10.42.0.x
- [ ] Ping được 10.42.0.1
- [ ] Kết nối thiết bị thứ 2 thành công
- [ ] Thiết bị thứ 3 bị từ chối (max 2 client)

---

## E. API TEST (dùng trình duyệt hoặc curl/Postman)

### E1. Trạng thái hệ thống
- [ ] `GET http://10.42.0.1:8765/` → hiện thông tin server
- [ ] `GET http://10.42.0.1:8765/api/status` → JSON có `"ok":true`, `"board":"ESP32-S3"`

### E2. Upload file
- [ ] `POST /api/upload` với 1 file nhỏ (< 1KB) → `"ok":true`
- [ ] `POST /api/upload` với file lớn (> 1MB) → `"ok":true`
- [ ] `POST /api/upload-all` với 3 file cùng lúc → `"uploaded":3`
- [ ] Upload file trùng tên → ghi đè thành công

### E3. Liệt kê file
- [ ] `GET /api/filelist` → JSON liệt kê đúng số file đã upload
- [ ] Tên file và dung lượng hiển thị đúng

### E4. Download file
- [ ] `GET /api/download?name=xxx` → tải về đúng file
- [ ] So sánh MD5 file gốc vs file tải về → khớp
- [ ] Download file không tồn tại → `"error":"file_not_found"`

### E5. Xóa file
- [ ] `DELETE /api/delete?name=xxx` → `"ok":true`
- [ ] `DELETE /api/delete-all` → xóa hết, `"deleted"` > 0
- [ ] Xóa file không tồn tại → `"error":"file_not_found"`

### E6. Lỗi
- [ ] Truy cập URL không tồn tại → `"error":"not_found"`
- [ ] Upload không có file → lỗi phù hợp

---

## F. ỔN ĐỊNH

- [ ] Upload liên tục 10 file không bị crash
- [ ] Ngắt kết nối WiFi rồi kết nối lại → vẫn hoạt động
- [ ] Rút thẻ SD khi đang chạy → không crash (có thể báo lỗi)
- [ ] Chạy liên tục 1 giờ không restart

---

## G. SO SÁNH VỚI PHIÊN BẢN CŨ (ESP32-C6)

| Tính năng | C6 (cũ) | S3 (mới) | Kết quả |
|-----------|---------|----------|---------|
| WiFi AP | OK | ? | [ ] |
| Upload file | OK | ? | [ ] |
| Download file | OK | ? | [ ] |
| Delete file | OK | ? | [ ] |
| LED trạng thái | NeoPixel RGB | 3 LED riêng | [ ] |
| SD Card SPI | GPIO19/18/20/23 | GPIO12/11/13/10 | [ ] |
| Tốc độ upload | Baseline | ? | [ ] |

---

## THAY ĐỔI CHÍNH SO VỚI BẢN CŨ

| Mục | ESP32-C6 (cũ) | ESP32-S3 (mới) |
|-----|---------------|----------------|
| Chip | ESP32-C6 | ESP32-S3 |
| LED | 1x NeoPixel RGB (GPIO8) | 3 LED riêng: RED(21), GREEN(47), WHITE(48) |
| SD SCK | GPIO19 | GPIO12 |
| SD MOSI | GPIO18 | GPIO11 |
| SD MISO | GPIO20 | GPIO13 |
| SD CS | GPIO23 | GPIO10 |
| Thư viện LED | Adafruit NeoPixel | GPIO trực tiếp (không cần lib) |
| I2S Mic | Không có | Reserved (GPIO4/5/6) |

---

*Checklist tạo ngày 2026-06-01 | Nhánh: r3b-v2.7.0.1*
