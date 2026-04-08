# ESP32 File Server — API Reference

> **Firmware v2.2** · Multi-format upload/download · SPIFFS storage  
> Áp dụng cho cả **Node-1** (AP `192.168.4.1`) và **Node-2** (AP `192.168.5.1`)

---

## Kết nối WiFi

| Node | SSID | Password | Base URL |
|------|------|----------|----------|
| **Node-1** (Thiết bị A) | `ESP32-Node-1` | `12345678` | `http://192.168.4.1` |
| **Node-2** (Thiết bị B) | `ESP32-Node-2` | `12345678` | `http://192.168.5.1` |

---

## Tổng quan Endpoints

| Method | Path | Mô tả |
|--------|------|--------|
| `GET` | `/status` | Trạng thái node |
| `GET` | `/file/list` | 📋 **Danh sách tất cả file** |
| `GET` | `/file/download?name=<tên>` | ⬇️ **Download file** |
| `GET` | `/file/info` | Thông tin file audio.wav |
| `GET` | `/ram/info` | RAM buffer info |
| `POST` | `/file/upload` | ⬆️ Upload file |
| `POST` | `/file/delete?name=<tên>` | Xóa file theo tên |
| `POST` | `/file/clear` | Xóa audio.wav |
| `POST` | `/sync` | Trigger đồng bộ (Node-2 only) |
| `TCP` | `:8080/` | Raw TCP — stream audio.wav |
| `TCP` | `:8081/` | Raw TCP upload (bypass WebServer) |

---

## 1. GET `/file/list` — Lấy danh sách file

Trả về danh sách **tất cả file** đang lưu trong SPIFFS cùng metadata.

### Request

```
GET http://192.168.4.1/file/list
```

Không cần header hay body.

### Response `200 OK`

```json
{
  "files": [
    {
      "name":         "audio.wav",
      "path":         "/audio.wav",
      "size":         102400,
      "size_kb":      "100.0 KB",
      "mime":         "audio/wav",
      "duration_sec": 3.20
    },
    {
      "name":   "photo.png",
      "path":   "/photo.png",
      "size":   48200,
      "size_kb": "47.1 KB",
      "mime":   "image/png"
    },
    {
      "name":   "report.docx",
      "path":   "/report.docx",
      "size":   15360,
      "size_kb": "15.0 KB",
      "mime":   "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    }
  ],
  "count":        3,
  "spiffs_total": 1966080,
  "spiffs_used":  165960,
  "spiffs_free":  1800120
}
```

### Các trường trong mỗi file

| Trường | Kiểu | Mô tả |
|--------|------|--------|
| `name` | string | Tên file (không có `/` đầu) |
| `path` | string | Đường dẫn SPIFFS đầy đủ (có `/`) |
| `size` | number | Kích thước bytes |
| `size_kb` | string | Kích thước KB (định dạng `"100.0 KB"`) |
| `mime` | string | MIME type tự động nhận diện theo extension |
| `duration_sec` | number | **(chỉ có với file `.wav`)** Thời lượng âm thanh (giây) |

### Code mẫu — Python

```python
import requests

r = requests.get("http://192.168.4.1/file/list", timeout=10)
data = r.json()

print(f"Tổng: {data['count']} file")
for f in data["files"]:
    print(f"  {f['name']}  {f['size_kb']}  [{f['mime']}]")
```

### Code mẫu — JavaScript / Fetch

```javascript
const resp = await fetch("http://192.168.4.1/file/list");
const data = await resp.json();

console.log(`Tổng: ${data.count} file`);
data.files.forEach(f => {
  console.log(`${f.name}  ${f.size_kb}  [${f.mime}]`);
});
```

### Code mẫu — Dart / Flutter

```dart
import 'dart:convert';
import 'package:http/http.dart' as http;

Future<List<Map>> getFileList() async {
  final resp = await http.get(Uri.parse("http://192.168.4.1/file/list"));
  final data = jsonDecode(resp.body);
  return List<Map>.from(data["files"]);
}
```

---

## 2. GET `/file/download?name=<tên>` — Download file

Download **bất kỳ định dạng** file nào đang lưu trong SPIFFS.

### Request

```
GET http://192.168.4.1/file/download?name=photo.png
```

| Query param | Bắt buộc | Mô tả |
|-------------|----------|--------|
| `name` | Không | Tên file muốn tải. Nếu bỏ qua → trả về `audio.wav` (tương thích cũ) |

### Response thành công `200 OK`

- Body: raw bytes của file
- Header `Content-Type`: MIME type tương ứng (vd: `image/png`, `audio/wav`, ...)
- Header `Content-Length`: kích thước bytes chính xác
- Header `Content-Disposition: attachment; filename="<tên file>"`

### Response lỗi

| HTTP | Body JSON | Nguyên nhân |
|------|-----------|-------------|
| `400` | `{"error":"invalid filename"}` | Tên file chứa ký tự không hợp lệ |
| `404` | `{"error":"file not found"}` | File không tồn tại trên SPIFFS |

### Code mẫu — Python (lưu về máy)

```python
import requests

def download_file(ip, filename, save_path):
    url = f"http://{ip}/file/download?name={filename}"
    r = requests.get(url, timeout=30, stream=True)
    if r.status_code == 200:
        with open(save_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=4096):
                f.write(chunk)
        print(f"Saved {save_path}  ({len(r.content)} bytes)")
    else:
        print(f"Error {r.status_code}: {r.text}")

# Ví dụ sử dụng
download_file("192.168.4.1", "audio.wav",  "./audio.wav")
download_file("192.168.4.1", "photo.png",  "./photo.png")
download_file("192.168.4.1", "report.xlsx","./report.xlsx")
```

### Code mẫu — JavaScript / Blob (lưu file trong trình duyệt)

```javascript
async function downloadFile(ip, filename) {
  const resp = await fetch(`http://${ip}/file/download?name=${filename}`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

  const blob = await resp.blob();
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// Ví dụ
downloadFile("192.168.4.1", "audio.wav");
downloadFile("192.168.4.1", "photo.png");
```

### Code mẫu — Dart / Flutter (lưu về storage)

```dart
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';

Future<File> downloadFile(String ip, String filename) async {
  final url  = Uri.parse("http://$ip/file/download?name=$filename");
  final resp = await http.get(url);

  if (resp.statusCode != 200) {
    throw Exception("Download failed: ${resp.statusCode}");
  }

  final dir  = await getTemporaryDirectory();
  final file = File("${dir.path}/$filename");
  await file.writeAsBytes(resp.bodyBytes);
  print("Saved to ${file.path}  (${resp.bodyBytes.length} bytes)");
  return file;
}
```

---

## 3. GET `/status` — Trạng thái node

### Request

```
GET http://192.168.4.1/status
```

### Response `200 OK`

```json
{
  "node":             1,
  "ap_ssid":          "ESP32-Node-1",
  "ap_ip":            "192.168.4.1",
  "uptime":           "00:12:34",
  "free_heap":        142336,
  "spiffs_has_file":  true,
  "spiffs_total":     1966080,
  "spiffs_used":      102400,
  "spiffs_free":      1863680,
  "ram_ready":        true,
  "ram_size":         102400,
  "node_enabled":     true,
  "builtin_wav_size": 4096
}
```

| Trường | Kiểu | Mô tả |
|--------|------|--------|
| `node` | number | ID node (`1` hoặc `2`) |
| `ap_ssid` | string | Tên WiFi AP đang phát |
| `ap_ip` | string | Địa chỉ IP của AP |
| `uptime` | string | Thời gian chạy `HH:MM:SS` |
| `free_heap` | number | RAM tự do (bytes) |
| `spiffs_has_file` | bool | Có file `audio.wav` hay không |
| `spiffs_total/used/free` | number | Dung lượng SPIFFS (bytes) |
| `ram_ready` | bool | WAV đã load vào RAM chưa |
| `ram_size` | number | Kích thước WAV trong RAM |
| `node_enabled` | bool | Node đang bật (AP đang hoạt động) |

---

## 4. GET `/file/info` — Thông tin file audio.wav

### Request

```
GET http://192.168.4.1/file/info
```

### Response `200 OK`

```json
{
  "has_file":  true,
  "path":      "/audio.wav",
  "size":      102400,
  "size_kb":   "100.0",
  "wav_info": {
    "is_wav":          true,
    "format":          "PCM",
    "channels":        1,
    "sample_rate":     16000,
    "bits_per_sample": 16,
    "data_size":       102400,
    "duration_sec":    3.20
  },
  "free_heap": 138240
}
```

---

## 5. GET `/ram/info` — RAM buffer info

### Request

```
GET http://192.168.4.1/ram/info
```

### Response khi đã có WAV trong RAM

```json
{
  "ram_ready":   true,
  "size_bytes":  102400,
  "magic":       "RIFF",
  "wav_info": {
    "is_wav":          true,
    "format":          "PCM",
    "channels":        1,
    "sample_rate":     16000,
    "bits_per_sample": 16,
    "data_size":       102352,
    "duration_sec":    3.20
  },
  "sync_msg":  "ok",
  "free_heap": 138240
}
```

### Response khi RAM chưa sẵn sàng

```json
{
  "ram_ready":        false,
  "free_heap":        200704,
  "spiffs_has_file":  false,
  "sync_msg":         "not started"
}
```

---

## 6. POST `/file/upload` — Upload file

Upload file bất kỳ định dạng lên SPIFFS.

### Request

```
POST http://192.168.4.1/file/upload
Header: X-Filename: myfile.png
Header: Content-Length: <số bytes>
Body: <raw binary data>
```

| Header | Bắt buộc | Mô tả |
|--------|----------|--------|
| `X-Filename` | Nên có | Tên file muốn lưu. Nếu thiếu → tự đặt tên `file_XXXX.bin` |
| `Content-Length` | Có | Kích thước body (bytes) |

> **Giới hạn:** file tối đa **1.8 MB** (1,800,000 bytes)

### Response `200 OK`

```json
{
  "status":       "ok",
  "filename":     "myfile.png",
  "size":         48200,
  "spiffs_saved": true
}
```

### Response lỗi

| HTTP | Body JSON | Nguyên nhân |
|------|-----------|-------------|
| `400` | `{"error":"missing filename or body"}` | Thiếu dữ liệu |
| `413` | `{"error":"file too large"}` | Vượt quá 1.8 MB |
| `507` | `{"error":"spiffs full"}` | SPIFFS đầy |

### Code mẫu — Python

```python
import requests

with open("photo.png", "rb") as f:
    data = f.read()

r = requests.post(
    "http://192.168.4.1/file/upload",
    headers={"X-Filename": "photo.png"},
    data=data,
    timeout=30
)
print(r.json())
# {"status": "ok", "filename": "photo.png", "size": 48200, "spiffs_saved": true}
```

---

## 7. POST `/file/delete?name=<tên>` — Xóa file

### Request

```
POST http://192.168.4.1/file/delete?name=photo.png
```

### Response `200 OK`

```json
{"status": "ok"}
```

### Response lỗi

| HTTP | Body JSON | Nguyên nhân |
|------|-----------|-------------|
| `400` | `{"error":"missing name"}` | Thiếu query param `name` |
| `404` | `{"error":"file not found"}` | File không tồn tại |

---

## 8. POST `/file/clear` — Xóa audio.wav

Xóa file `audio.wav` khỏi SPIFFS và giải phóng RAM buffer.

### Request

```
POST http://192.168.4.1/file/clear
```

### Response `200 OK`

```json
{"status": "ok", "message": "File da xoa"}
```

---

## 9. POST `/sync` — Trigger đồng bộ *(Node-2 only)*

Kích hoạt Node-2 kết nối vào Node-1 và đồng bộ file ngay lập tức.

### Request

```
POST http://192.168.5.1/sync
```

### Response `200 OK`

```json
{"status": "ok", "message": "Sync starting in background"}
```

---

## 10. TCP Raw — Port 8080 (stream audio.wav)

Giao thức HTTP-over-raw-TCP, tương thích firmware cũ. Chỉ phục vụ `audio.wav`.

### GET (lấy WAV)

```
GET / HTTP/1.1
Host: 192.168.4.1
Connection: close

```

**Response:** HTTP/1.1 200 với body là raw WAV bytes.

### POST (upload WAV)

```
POST / HTTP/1.1
Host: 192.168.4.1
Content-Length: 102400
X-Filename: audio.wav
Connection: close

<raw bytes>
```

---

## 11. TCP Raw Upload — Port 8081

Upload file lớn qua raw TCP, bypass giới hạn body của WebServer. Cú pháp giống port 8080 POST, nhưng hỗ trợ mọi định dạng.

```
POST / HTTP/1.0
Host: 192.168.4.1
Content-Length: 48200
X-Filename: photo.png
Connection: close

<raw bytes>
```

**Response:**

```json
{
  "status":       "ok",
  "filename":     "photo.png",
  "size":         48200,
  "spiffs_saved": true
}
```

---

## MIME Types được hỗ trợ

| Extension | MIME Type |
|-----------|-----------|
| `.wav` | `audio/wav` |
| `.mp3` | `audio/mpeg` |
| `.ogg` | `audio/ogg` |
| `.flac` | `audio/flac` |
| `.aac` | `audio/aac` |
| `.png` | `image/png` |
| `.jpg` / `.jpeg` | `image/jpeg` |
| `.gif` | `image/gif` |
| `.bmp` | `image/bmp` |
| `.webp` | `image/webp` |
| `.svg` | `image/svg+xml` |
| `.pdf` | `application/pdf` |
| `.txt` | `text/plain` |
| `.csv` | `text/csv` |
| `.json` | `application/json` |
| `.xml` | `application/xml` |
| `.zip` | `application/zip` |
| `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| `.bin` / khác | `application/octet-stream` |

---

## Quy tắc Sanitize tên file

Firmware tự làm sạch tên file khi nhận upload:

- Giữ lại: chữ cái, số, `-`
- Thay bằng `_`: dấu cách, `_`, `.`, `(`, `)`
- Không lặp `_` liên tiếp
- Base name tối đa **32 ký tự**
- Extension tối đa **8 ký tự** (sau dấu chấm)
- Không có extension hợp lệ → thêm `.bin`

**Ví dụ:**

| Tên gốc | Tên sau sanitize |
|---------|-----------------|
| `My Photo (1).PNG` | `My_Photo_1.png` |
| `report 2024.docx` | `report_2024.docx` |
| `file with spaces` | `file_with_spaces.bin` |
| `audio.wav` | `audio.wav` |

---

## Ví dụ tích hợp đầy đủ — Python

```python
import requests

BASE = "http://192.168.4.1"   # Hoặc 192.168.5.1 cho Node-2

# 1. Lấy danh sách file
files = requests.get(f"{BASE}/file/list", timeout=10).json()["files"]
print(f"Có {len(files)} file:")
for f in files:
    print(f"  {f['name']:30s} {f['size_kb']:>10s}  {f['mime']}")

# 2. Download từng file
import os, pathlib
save_dir = pathlib.Path("./downloads")
save_dir.mkdir(exist_ok=True)

for f in files:
    r = requests.get(f"{BASE}/file/download?name={f['name']}", timeout=30, stream=True)
    if r.status_code == 200:
        dest = save_dir / f["name"]
        dest.write_bytes(r.content)
        print(f"  ✓ {f['name']}  →  {dest}")
    else:
        print(f"  ✗ {f['name']}  HTTP {r.status_code}")
```

---

## Ví dụ tích hợp đầy đủ — JavaScript

```javascript
const BASE = "http://192.168.4.1";

async function listAndDownload() {
  // 1. Lấy danh sách
  const { files } = await fetch(`${BASE}/file/list`).then(r => r.json());
  console.log(`Có ${files.length} file`);

  // 2. Download file đầu tiên
  for (const f of files) {
    const resp = await fetch(`${BASE}/file/download?name=${f.name}`);
    if (!resp.ok) { console.error(`Lỗi ${f.name}: ${resp.status}`); continue; }

    const blob = await resp.blob();
    // --- Lưu trong Node.js ---
    // const buf = Buffer.from(await blob.arrayBuffer());
    // require('fs').writeFileSync(f.name, buf);

    // --- Lưu trong trình duyệt ---
    const a = Object.assign(document.createElement("a"), {
      href: URL.createObjectURL(blob), download: f.name
    });
    a.click();

    console.log(`✓ ${f.name}  (${f.size_kb})`);
  }
}

listAndDownload();
```

---

*Tài liệu này được tạo tự động từ source code firmware v2.2.*  
*Node-1: [`esp32_server/src/main.cpp`](../esp32_server/src/main.cpp) · Node-2: [`esp32_client/src/main.cpp`](../esp32_client/src/main.cpp)*
