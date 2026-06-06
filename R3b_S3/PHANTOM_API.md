# PHANTOM Node API

Tài liệu này dành cho thiết bị `...0001` tích hợp các chức năng của node `...0002` vào UI.

---

## Kết nối

Khi node `...0002` kết nối vào WiFi `7068616e746f6d303030303030300001`, nó:
1. Chạy web server trên cổng **8765**
2. POST sự kiện kết nối lên `...0001` qua `/api/device/connection`

```
Base URL: http://<IP_CUA_NODE>:8765
```

---

## Sự kiện kết nối tự động

Ngay sau khi node kết nối STA thành công, nó POST lên gateway (`...0001`):

```
POST http://<IP_0001>:8765/api/device/connection
Content-Type: application/json

{ "ip": "192.168.x.x", "type": "B" }
```

| Field  | Giá trị | Mô tả |
|--------|---------|-------|
| `ip`   | string  | IP của node trên mạng `...0001` |
| `type` | `"B"`   | Loại thiết bị — `"A"` là Pi, `"B"` là ESP32-S3 node |

Bên `...0001` cần expose endpoint `POST /api/device/connection` để nhận sự kiện này và cập nhật UI.

---

## Endpoints

### GET /api/status

Kiểm tra kết nối, trạng thái thẻ nhớ và thông tin WiFi AP của node.

**Response 200**
```json
{
  "ok": true,
  "board": "ESP32-S3",
  "wifi_mode": "STA",
  "ip": "192.168.x.x",
  "port": 8765,
  "ap_ssid": "7068616e746f6d303030303030300002",
  "ap_pass": "12345678",
  "sd_card_type": "SDHC/SDXC",
  "sd_spi_hz": 20000000,
  "sd_total": 31523495936,
  "sd_used": 1048576,
  "sd_total_human": "29.37 GB",
  "sd_used_human": "1.00 MB"
}
```

| Field       | Mô tả |
|-------------|-------|
| `wifi_mode` | `"STA"` khi kết nối `...0001`, `"AP"` khi đang phát |
| `ip`        | IP hiện tại — STA: do DHCP cấp, AP: `10.42.0.1` |
| `ap_ssid`   | Tên WiFi node tự phát khi không có `...0001` |
| `ap_pass`   | Mật khẩu WiFi node tự phát |

---

### GET /api/filelist

Lấy danh sách file trong thư mục `/uploads` trên thẻ nhớ.

**Response 200**
```json
{
  "ok": true,
  "dir": "/uploads",
  "files": [
    {
      "name": "data.bin",
      "size": 204800,
      "size_human": "200.0 KB",
      "download": "/api/download?name=data.bin"
    }
  ]
}
```

**Response 500** — không mở được thư mục
```json
{ "ok": false, "error": "cannot_open_uploads_dir" }
```

---

### GET /api/download?name=\<filename\>

Tải file về. Response là raw binary với header `Content-Disposition: attachment`.

| Param | Kiểu   | Mô tả            |
|-------|--------|------------------|
| name  | string | Tên file cần tải |

**Response 200** — binary stream  
**Response 400** — thiếu tham số `name`  
**Response 404** — file không tồn tại  
**Response 500** — không mở được file  

---

### DELETE /api/delete?name=\<filename\>

Xóa một file. Cũng nhận `GET` và `POST` cùng path.

| Param | Kiểu   | Mô tả             |
|-------|--------|-------------------|
| name  | string | Tên file cần xóa  |

**Response 200**
```json
{ "ok": true, "deleted": "data.bin" }
```

**Response 400** — thiếu `name`  
**Response 404** — file không tồn tại  
**Response 500** — xóa thất bại  

---

### DELETE /api/delete-all

Xóa toàn bộ file trong `/uploads`. Cũng nhận `POST`.

**Response 200**
```json
{ "ok": true, "deleted": 5, "failed": 0 }
```

**Response 500** — có file xóa thất bại
```json
{ "ok": false, "deleted": 3, "failed": 2 }
```

---

### POST /api/upload

Upload một file. Body là `multipart/form-data` với key `file`.

```
POST /api/upload
Content-Type: multipart/form-data
Form field: file = <binary>
```

**Response 200**
```json
{
  "ok": true,
  "name": "data.bin",
  "size": 204800,
  "size_human": "200.0 KB"
}
```

**Response 500** — lỗi ghi thẻ nhớ
```json
{ "ok": false, "error": "sd_write_failed" }
```

---

### POST /api/upload-all

Upload nhiều file cùng lúc. Body là `multipart/form-data` với key `files`.

```
POST /api/upload-all
Content-Type: multipart/form-data
Form field: files = <binary 1>
Form field: files = <binary 2>
```

**Response 200**
```json
{
  "ok": true,
  "uploaded": 3,
  "failed": 0,
  "total_bytes": 614400,
  "total_human": "600.0 KB"
}
```

**Response 500** — có file thất bại
```json
{
  "ok": false,
  "uploaded": 2,
  "failed": 1,
  "total_bytes": 409600,
  "total_human": "400.0 KB",
  "errors": "bad_file.bin: sd_write_failed; "
}
```

---

### GET /api/dinh-danh

Lấy thông tin định danh hiện tại từ RAM (được load từ SD khi boot).

**Response 200**
```json
{
  "ok": true,
  "ma_id": "BN-0001",
  "ho_ten": "Nguyen Van A",
  "nam_sinh": 1990,
  "dia_chi": "Ha Noi",
  "ngay_cap_dinh_danh": "2026-05-30",
  "ten_thiet_bi": "Pi Zero 2W",
  "id_thiet_bi": "DEVICE-0001",
  "dia_chi_thiet_bi": "Ha Noi"
}
```

---

### GET /api/device-info

Lấy `id_thiet_bi` và MAC address của node. Chỉ có nghĩa khi node đang ở chế độ STA (kết nối `...0001`).

**Response 200**
```json
{
  "ok": true,
  "id_thiet_bi": "DEVICE-0001",
  "mac": "AA:BB:CC:DD:EE:FF"
}
```

---

### POST /api/dinh-danh

Cập nhật thông tin định danh. Chỉ cần gửi các field muốn thay đổi — field không gửi giữ nguyên giá trị cũ. Cập nhật RAM ngay lập tức và ghi xuống `/dinh_danh.json` trên SD để bền vững qua reboot.

```
POST /api/dinh-danh
Content-Type: application/json
```

**Body** (tất cả field đều optional):
```json
{
  "ma_id": "BN-0001",
  "ho_ten": "Nguyen Van A",
  "nam_sinh": 1990,
  "dia_chi": "Ha Noi",
  "ngay_cap_dinh_danh": "2026-05-30",
  "ten_thiet_bi": "Pi Zero 2W",
  "id_thiet_bi": "DEVICE-0001",
  "dia_chi_thiet_bi": "Ha Noi"
}
```

**Response 200** — trả lại toàn bộ định danh sau khi cập nhật (giống GET)

**Response 400** — body rỗng
```json
{ "ok": false, "error": "empty body" }
```

**Response 500** — ghi SD thất bại
```json
{ "ok": false, "error": "sd_write_failed" }
```

| Lưu ý | Chi tiết |
|-------|----------|
| RAM   | Cập nhật ngay, `GET /api/dinh-danh` và `GET /api/status` phản ánh giá trị mới ngay sau POST |
| SD    | Ghi vào `/dinh_danh.json` — giá trị tồn tại qua reboot |
| Boot  | ESP đọc `/dinh_danh.json` một lần vào RAM, nếu file chưa có thì dùng giá trị default |

---

## Mã lỗi

| error                        | Mô tả                              |
|------------------------------|------------------------------------|
| `missing_name`               | Thiếu tham số `name`               |
| `file_not_found`             | File không tồn tại trên thẻ nhớ    |
| `cannot_open_file`           | Không đọc được file                |
| `cannot_open_file_for_write` | Không ghi được file                |
| `cannot_create_uploads_dir`  | Không tạo được thư mục             |
| `sd_write_failed`            | Lỗi ghi thẻ nhớ                    |
| `empty_upload`               | File upload rỗng (0 byte)          |
| `upload_aborted`             | Client ngắt kết nối giữa chừng     |
| `no_files_uploaded`          | Không có file nào trong request    |
| `cannot_open_uploads_dir`    | Không mở được thư mục `/uploads`   |
| `delete_failed`              | Xóa file thất bại                  |

---

## Gợi ý tích hợp UI

### 1. Phát hiện node tự động

Expose endpoint `POST /api/device/connection` trên server `...0001`. Node `...0002` tự gọi ngay khi kết nối WiFi — không cần polling để biết node online.

### 2. Polling trạng thái

Poll `GET /api/status` mỗi 30 giây để kiểm tra node còn online và cập nhật dung lượng thẻ nhớ.

### 3. Flow tải file

```
GET /api/filelist
  → hiển thị danh sách
  → user chọn file
GET /api/download?name=<file>
  → lưu về máy
```

### 4. LED node

| Màu LED     | Trạng thái                          |
|-------------|-------------------------------------|
| Hồng        | Đang kết nối STA với `...0001`      |
| Xanh lá     | Đang phát AP, có client kết nối     |
| Tắt         | Đang phát AP, chưa có client        |
| Trắng nhấp nháy | Đang nhận upload file          |

### 5. Timeout khuyến nghị

| Thao tác                    | Timeout |
|-----------------------------|---------|
| status / filelist / delete  | 6s      |
| upload 1 file               | 300s    |
| upload nhiều file           | 600s    |
| download                    | 300s    |
