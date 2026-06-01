# BÁO CÁO TÓM TẮT DỰ ÁN PHANTOM_ESP
## Phiên bản: r3b v2.7.0.1 | Nhánh: r3b-v2.7.0.1

---

## 1. DỰ ÁN NÀY LÀ GÌ?

Phantom_esp là một **máy chủ file không dây (wireless file server)** chạy trên chip ESP32-C6.

Nói đơn giản: Cắm thẻ nhớ microSD vào board ESP32-C6, bật nguồn → nó phát WiFi riêng → kết nối vào WiFi đó → upload/download file qua trình duyệt hoặc app Python.

---

## 2. PHẦN CỨNG SỬ DỤNG

| Thành phần | Chi tiết |
|------------|----------|
| Board | ESP32-C6 DevKitC-1-N8 |
| Flash | 8MB |
| Thẻ nhớ | microSD qua giao tiếp SPI |
| Đèn LED | NeoPixel RGB trên GPIO8 |
| Kết nối | WiFi (chế độ Access Point) |

### Sơ đồ chân kết nối thẻ nhớ:
```
GPIO19 → SCK  (xung nhịp)
GPIO18 → MOSI (dữ liệu gửi)
GPIO20 → MISO (dữ liệu nhận)
GPIO23 → CS   (chọn chip)
```

---

## 3. CÁCH HOẠT ĐỘNG

```
┌─────────────────────────────────────────────────────┐
│                  LUỒNG HOẠT ĐỘNG                     │
├─────────────────────────────────────────────────────┤
│                                                      │
│  [Bật nguồn]                                        │
│      │                                               │
│      ▼                                               │
│  Khởi tạo Serial (115200 baud)                      │
│      │                                               │
│      ▼                                               │
│  Khởi tạo SPI + Mount thẻ SD                        │
│  (thử nhiều tốc độ: 20MHz → 400kHz)                │
│      │                                               │
│      ▼                                               │
│  Phát WiFi AP                                        │
│  SSID: "7068616e746f6d303030303030300002"           │
│  Pass: "12345678"                                    │
│  IP:   10.42.0.1:8765                               │
│      │                                               │
│      ▼                                               │
│  Bật Web Server → Chờ kết nối                       │
│      │                                               │
│      ▼                                               │
│  [Vòng lặp chính]                                   │
│  ├─ Xử lý request HTTP                              │
│  └─ Cập nhật đèn LED trạng thái                    │
│                                                      │
└─────────────────────────────────────────────────────┘
```

---

## 4. TÍNH NĂNG CHÍNH

### 4.1 Quản lý file qua WiFi
| API Endpoint | Chức năng |
|-------------|-----------|
| `GET /api/status` | Xem trạng thái hệ thống (dung lượng SD, WiFi...) |
| `GET /api/filelist` | Liệt kê tất cả file đã upload |
| `POST /api/upload` | Upload 1 file |
| `POST /api/upload-all` | Upload nhiều file cùng lúc |
| `GET /api/download?name=xxx` | Tải file về |
| `DELETE /api/delete?name=xxx` | Xóa 1 file |
| `DELETE /api/delete-all` | Xóa tất cả file |

### 4.2 Đèn LED trạng thái
| Màu | Ý nghĩa |
|-----|---------|
| Xanh lá (Green) | Có thiết bị đang kết nối WiFi |
| Xanh dương (Blue) | Upload file thành công (nhấp nháy 500ms) |
| Tắt | Không có ai kết nối |

### 4.3 Thẻ nhớ microSD
- Tự động thử nhiều tốc độ SPI nếu mount thất bại
- Tạo thư mục upload tự động
- Hỗ trợ file tối đa 80 ký tự tên

---

## 5. CẤU HÌNH WiFi

```
Chế độ:        Access Point (phát WiFi riêng)
SSID:          7068616e746f6d303030303030300002
Mật khẩu:     12345678
IP:            10.42.0.1
Cổng:          8765
Kênh:          1
Số client tối đa: 2
Công suất TX:  19.5 dBm
```

---

## 6. CẤU TRÚC THƯ MỤC

```
Phantom_esp/
├── R3b/                        ← THƯ MỤC DỰ ÁN CHÍNH
│   ├── platformio.ini          ← Cấu hình build PlatformIO
│   ├── src/
│   │   └── main.cpp            ← SOURCE CODE CHÍNH (1004 dòng)
│   └── main/
│       ├── main.c              ← Phiên bản C (thay thế)
│       └── CMakeLists.txt
├── .env.example                ← Mẫu cấu hình WiFi
├── decode.py                   ← Tool giải mã firmware (AES-GCM → WAV)
├── makefile.py                 ← App Python GUI để quản lý file
└── code.md                     ← Ghi chú lệnh build
```

---

## 7. THƯ VIỆN SỬ DỤNG

| Thư viện | Mục đích |
|----------|----------|
| WiFi.h | Điều khiển WiFi AP |
| WebServer.h | HTTP server |
| SPI.h | Giao tiếp SPI với thẻ SD |
| SD.h | Đọc/ghi thẻ nhớ |
| FS.h | Hệ thống file |
| Adafruit_NeoPixel | Điều khiển đèn LED RGB |

---

## 8. CÔNG CỤ HỖ TRỢ

### decode.py
- Giải mã firmware được mã hóa AES-GCM
- Chuyển đổi sang định dạng WAV

### makefile.py
- Ứng dụng Python GUI
- Kết nối đến ESP32 qua WiFi
- Quản lý file (upload/download) từ máy tính

---

## 9. CÁCH BUILD & UPLOAD

```bash
# Build và upload firmware
pio run -t upload

# Hoặc chỉ định port
pio run -t upload --upload-port COM6

# Monitor serial
pio device monitor -b 115200
```

---

## 10. TÓM TẮT NHANH

> **Phantom_esp** = ESP32-C6 + thẻ nhớ SD + WiFi AP = Máy chủ file không dây bỏ túi.
>
> Không có tính năng tấn công mạng. Đây là công cụ truyền file thuần túy,
> điều khiển qua REST API hoặc app Python đi kèm.

---

*Báo cáo tạo tự động ngày 2026-06-01 | Nhánh: r3b-v2.7.0.1*
