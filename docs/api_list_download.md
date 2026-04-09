# API — Lấy Danh Sách & Download File (Node-1 / Node-2)

> **Firmware v2.2** · Áp dụng cho cả 2 node  
> Cập nhật: 2026-04-09

---

## 🌐 Thông tin kết nối

| | **Node-1 (Thiết bị A)** | **Node-2 (Thiết bị B)** |
|---|---|---|
| **WiFi SSID** | `ESP32-Node-1` | `ESP32-Node-2` |
| **Password** | `12345678` | `12345678` |
| **IP** | `192.168.4.1` | `192.168.5.1` |
| **WiFi Channel** | 1 | 6 |
| **Base URL** | `http://192.168.4.1` | `http://192.168.5.1` |

---

## 📋 1. GET `/file/list` — Lấy danh sách file

### Endpoint

| Node | URL đầy đủ |
|------|-----------|
| **Node-1** | `GET http://192.168.4.1/file/list` |
| **Node-2** | `GET http://192.168.5.1/file/list` |

- Không cần header, không cần body
- Trả về **tất cả file** đang lưu trong SPIFFS của node đó

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
      "name":    "photo.png",
      "path":    "/photo.png",
      "size":    48200,
      "size_kb": "47.1 KB",
      "mime":    "image/png"
    },
    {
      "name":    "report.docx",
      "path":    "/report.docx",
      "size":    15360,
      "size_kb": "15.0 KB",
      "mime":    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    }
  ],
  "count":        3,
  "spiffs_total": 1966080,
  "spiffs_used":  165960,
  "spiffs_free":  1800120
}
```

### Mô tả các trường

| Trường | Kiểu | Có ở file nào | Mô tả |
|--------|------|---------------|-------|
| `name` | string | tất cả | Tên file (không có `/` đầu) |
| `path` | string | tất cả | Đường dẫn SPIFFS (`/tên_file`) |
| `size` | number | tất cả | Kích thước bytes |
| `size_kb` | string | tất cả | Kích thước KB dạng `"100.0 KB"` |
| `mime` | string | tất cả | MIME type tự nhận diện theo extension |
| `duration_sec` | number | **chỉ `.wav`** | Thời lượng âm thanh (giây) |
| `count` | number | root | Tổng số file |
| `spiffs_total` | number | root | Dung lượng SPIFFS tổng (bytes) |
| `spiffs_used` | number | root | Đã dùng (bytes) |
| `spiffs_free` | number | root | Còn trống (bytes) |

### Response khi không có file

```json
{
  "files":        [],
  "count":        0,
  "spiffs_total": 1966080,
  "spiffs_used":  0,
  "spiffs_free":  1966080
}
```

---

## ⬇️ 2. GET `/file/download?name=<tên>` — Download file

### Endpoint

| Node | URL đầy đủ (ví dụ) |
|------|-------------------|
| **Node-1** | `GET http://192.168.4.1/file/download?name=photo.png` |
| **Node-2** | `GET http://192.168.5.1/file/download?name=photo.png` |

### Query Parameters

| Tham số | Bắt buộc | Mô tả |
|---------|----------|-------|
| `name` | Không | Tên file muốn tải. **Nếu bỏ trống** → trả về `audio.wav` (tương thích cũ) |

### Response thành công `200 OK`

| Header | Giá trị ví dụ |
|--------|--------------|
| `Content-Type` | `image/png` (MIME tương ứng extension) |
| `Content-Length` | `48200` (bytes chính xác) |
| `Content-Disposition` | `attachment; filename="photo.png"` |
| Body | Raw binary bytes của file |

### Response lỗi

| HTTP | JSON | Nguyên nhân |
|------|------|-------------|
| `400` | `{"error":"invalid filename"}` | Tên chứa ký tự không hợp lệ |
| `404` | `{"error":"file not found"}` | File không tồn tại trên SPIFFS |

---

## 🐍 Code mẫu — Python

### Lấy danh sách file từ 1 node

```python
import requests

def get_file_list(node_ip: str) -> list:
    """Lấy danh sách file từ node ESP32"""
    url = f"http://{node_ip}/file/list"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()
    print(f"[{node_ip}] {data['count']} file — "
          f"SPIFFS: {data['spiffs_used']//1024}KB/{data['spiffs_total']//1024}KB")
    for f in data["files"]:
        dur = f"  {f['duration_sec']:.1f}s" if "duration_sec" in f else ""
        print(f"  {f['name']:30s}  {f['size_kb']:>10s}  {f['mime']}{dur}")
    return data["files"]

# Node-1
files_node1 = get_file_list("192.168.4.1")

# Node-2
files_node2 = get_file_list("192.168.5.1")
```

### Download 1 file từ node bất kỳ

```python
import requests
from pathlib import Path

def download_file(node_ip: str, filename: str, save_dir: str = ".") -> Path:
    """Download file từ ESP32 node về máy"""
    url  = f"http://{node_ip}/file/download?name={filename}"
    dest = Path(save_dir) / filename

    r = requests.get(url, timeout=30, stream=True)
    if r.status_code == 200:
        dest.write_bytes(r.content)
        print(f"✓ {filename}  ({len(r.content):,} bytes)  →  {dest}")
    elif r.status_code == 404:
        print(f"✗ {filename}  — File không tồn tại trên {node_ip}")
    else:
        print(f"✗ {filename}  — HTTP {r.status_code}: {r.text}")
    return dest

# Ví dụ
download_file("192.168.4.1", "audio.wav",   "./downloads")   # từ Node-1
download_file("192.168.5.1", "photo.png",   "./downloads")   # từ Node-2
download_file("192.168.4.1", "report.docx", "./downloads")   # từ Node-1
```

### Lấy danh sách cả 2 node rồi download tất cả

```python
import requests
from pathlib import Path

NODE1 = "192.168.4.1"
NODE2 = "192.168.5.1"

def download_all_from_node(node_ip: str, save_dir: str = "./downloads"):
    """Lấy danh sách rồi download toàn bộ file từ 1 node"""
    Path(save_dir).mkdir(exist_ok=True)

    # Bước 1: lấy danh sách
    try:
        r = requests.get(f"http://{node_ip}/file/list", timeout=10)
        r.raise_for_status()
        files = r.json().get("files", [])
    except Exception as e:
        print(f"[{node_ip}] Không kết nối được: {e}")
        return

    print(f"\n[{node_ip}] Có {len(files)} file:")

    # Bước 2: download từng file
    for f in files:
        fname = f["name"]
        url   = f"http://{node_ip}/file/download?name={fname}"
        dest  = Path(save_dir) / fname
        try:
            resp = requests.get(url, timeout=30, stream=True)
            if resp.status_code == 200:
                dest.write_bytes(resp.content)
                print(f"  ✓ {fname:30s} {f['size_kb']:>10s}")
            else:
                print(f"  ✗ {fname:30s} HTTP {resp.status_code}")
        except Exception as e:
            print(f"  ✗ {fname:30s} Lỗi: {e}")

# Chạy cho cả 2 node
# (Nhớ kết nối WiFi đúng node trước khi gọi)
download_all_from_node(NODE1, "./downloads/node1")
# download_all_from_node(NODE2, "./downloads/node2")
```

---

## 🌐 Code mẫu — JavaScript / Fetch

### Lấy danh sách file

```javascript
/**
 * Lấy danh sách file từ ESP32 node
 * @param {string} nodeIp - "192.168.4.1" hoặc "192.168.5.1"
 */
async function getFileList(nodeIp) {
  const resp = await fetch(`http://${nodeIp}/file/list`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

  const data = await resp.json();
  console.log(`[${nodeIp}] ${data.count} file`);
  data.files.forEach(f => {
    const dur = f.duration_sec ? `  ${f.duration_sec.toFixed(1)}s` : "";
    console.log(`  ${f.name.padEnd(30)} ${f.size_kb.padStart(10)}  [${f.mime}]${dur}`);
  });
  return data.files;
}

// Node-1
const node1Files = await getFileList("192.168.4.1");
// Node-2
const node2Files = await getFileList("192.168.5.1");
```

### Download file (lưu trong trình duyệt)

```javascript
/**
 * Download file từ ESP32 node, lưu về máy qua trình duyệt
 * @param {string} nodeIp
 * @param {string} filename
 */
async function downloadFile(nodeIp, filename) {
  const url  = `http://${nodeIp}/file/download?name=${encodeURIComponent(filename)}`;
  const resp = await fetch(url);

  if (!resp.ok) {
    console.error(`✗ ${filename} — HTTP ${resp.status}`);
    return;
  }

  const blob = await resp.blob();
  const a    = document.createElement("a");
  a.href     = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);

  console.log(`✓ ${filename}  (${blob.size.toLocaleString()} bytes)`);
}

// Ví dụ
await downloadFile("192.168.4.1", "audio.wav");   // Node-1
await downloadFile("192.168.5.1", "photo.png");   // Node-2
```

### Download file (Node.js — lưu ra đĩa)

```javascript
const https = require("http");
const fs    = require("fs");
const path  = require("path");

function downloadFile(nodeIp, filename, saveDir = "./downloads") {
  return new Promise((resolve, reject) => {
    const url  = `http://${nodeIp}/file/download?name=${encodeURIComponent(filename)}`;
    const dest = path.join(saveDir, filename);
    fs.mkdirSync(saveDir, { recursive: true });

    const file = fs.createWriteStream(dest);
    https.get(url, res => {
      if (res.statusCode !== 200) {
        reject(new Error(`HTTP ${res.statusCode}`)); return;
      }
      res.pipe(file);
      file.on("finish", () => {
        file.close();
        console.log(`✓ ${filename}  →  ${dest}`);
        resolve(dest);
      });
    }).on("error", reject);
  });
}

// Ví dụ
await downloadFile("192.168.4.1", "audio.wav",  "./downloads/node1");
await downloadFile("192.168.5.1", "photo.png",  "./downloads/node2");
```

---

## 🎯 Dart / Flutter

### Lấy danh sách file

```dart
import 'dart:convert';
import 'package:http/http.dart' as http;

Future<List<Map<String, dynamic>>> getFileList(String nodeIp) async {
  final uri  = Uri.parse("http://$nodeIp/file/list");
  final resp = await http.get(uri).timeout(const Duration(seconds: 10));

  if (resp.statusCode != 200) {
    throw Exception("HTTP ${resp.statusCode}");
  }

  final data  = jsonDecode(resp.body) as Map<String, dynamic>;
  final files = List<Map<String, dynamic>>.from(data["files"]);

  print("[$nodeIp] ${data['count']} file");
  for (final f in files) {
    print("  ${f['name']}  ${f['size_kb']}  [${f['mime']}]");
  }
  return files;
}

// Sử dụng
final node1Files = await getFileList("192.168.4.1");
final node2Files = await getFileList("192.168.5.1");
```

### Download file

```dart
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';

Future<File?> downloadFile(String nodeIp, String filename) async {
  final uri  = Uri.parse(
    "http://$nodeIp/file/download?name=${Uri.encodeComponent(filename)}"
  );
  final resp = await http.get(uri).timeout(const Duration(seconds: 30));

  if (resp.statusCode != 200) {
    print("✗ $filename — HTTP ${resp.statusCode}");
    return null;
  }

  final dir  = await getTemporaryDirectory();
  final file = File("${dir.path}/$filename");
  await file.writeAsBytes(resp.bodyBytes);

  print("✓ $filename  (${resp.bodyBytes.length} bytes)  →  ${file.path}");
  return file;
}

// Sử dụng
final wavFile  = await downloadFile("192.168.4.1", "audio.wav");   // Node-1
final pngFile  = await downloadFile("192.168.5.1", "photo.png");   // Node-2
```

---

## ⚡ cURL (terminal / test nhanh)

```bash
# ── Lấy danh sách file ───────────────────────────────────────────────
# Node-1
curl http://192.168.4.1/file/list

# Node-2
curl http://192.168.5.1/file/list

# Hiển thị đẹp (cần jq)
curl -s http://192.168.4.1/file/list | jq '.files[] | {name, size_kb, mime}'

# ── Download file ────────────────────────────────────────────────────
# Node-1 — download audio.wav
curl -o audio_node1.wav "http://192.168.4.1/file/download?name=audio.wav"

# Node-1 — download photo.png
curl -o photo.png "http://192.168.4.1/file/download?name=photo.png"

# Node-2 — download file bất kỳ
curl -o report.docx "http://192.168.5.1/file/download?name=report.docx"

# Download với progress bar
curl --progress-bar -o audio.wav "http://192.168.4.1/file/download?name=audio.wav"
```

---

## 🔄 So sánh 2 node

| | Node-1 | Node-2 |
|---|---|---|
| **IP** | `192.168.4.1` | `192.168.5.1` |
| **List URL** | `http://192.168.4.1/file/list` | `http://192.168.5.1/file/list` |
| **Download URL** | `http://192.168.4.1/file/download?name=X` | `http://192.168.5.1/file/download?name=X` |
| **Nguồn file** | Upload từ laptop hoặc pull từ Node-2 | Pull từ Node-1 hoặc upload trực tiếp |
| **Sync endpoint** | `GET /sync/status` (báo cáo) | `POST /sync` (trigger sync ngay) |
| **Đặc điểm riêng** | Hoãn sync khi laptop đang kết nối AP | Sync ngay khi boot nếu chưa có file |

---

*Source code: [`esp32_server/src/main.cpp`](../esp32_server/src/main.cpp) (Node-1) · [`esp32_client/src/main.cpp`](../esp32_client/src/main.cpp) (Node-2)*
