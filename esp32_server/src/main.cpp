/*
 * v2.2 — multi-format upload (txt/png/jpg/docx/xlsx/wav/...)
 * ESP32 NODE-1 — AP + SPIFFS File Server
 * ══════════════════════════════════════════════════════════════
 * Vai trò : Phát WiFi AP "ESP32-Node-1", nhận upload file từ
 *           laptop/PC (bất kỳ định dạng), lưu SPIFFS,
 *           rồi cho Node-2 lấy về (relay)
 *
 * BOOT button (GPIO 0):
 *   Nhấn 1 lần ngắn → Toggle ON/OFF
 *   ON  : AP bật, server chạy, LED sáng
 *   OFF : AP tắt, tiết kiệm điện, LED tắt
 *
 * Endpoints (port 80):
 *   GET  /status              ← trạng thái
 *   GET  /file/info           ← thông tin file (audio.wav)
 *   GET  /file/list           ← danh sách tất cả file
 *   GET  /file/download?name= ← download file theo tên (mọi định dạng)
 *   POST /file/upload         ← upload file (X-Filename header + raw body)
 *   POST /file/clear          ← xóa tất cả / xóa audio.wav
 *   POST /file/delete?name=   ← xóa file theo tên
 *   GET  /ram/info            ← RAM buffer info
 *
 * Port 8080: Raw TCP (tương thích firmware cũ)
 *
 * Upload từ Python:
 *   import requests
 *   r = requests.post("http://192.168.4.1/file/upload",
 *                     headers={"X-Filename": "photo.png"},
 *                     data=open("photo.png","rb").read())
 *   print(r.text)
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <SPIFFS.h>
#include <vector>
#include "test_wav.h"

// ── Cấu hình Node-1 ───────────────────────────────────────────
#define NODE_ID            1

#define MY_AP_SSID         "ESP32-Node-1"
#define MY_AP_PASSWORD     "12345678"
#define MY_AP_CHANNEL      1
#define MY_AP_HIDDEN       false
#define MY_AP_MAX_CON      4

// Node-2 — Node-1 kết nối vào để lấy file (đồng bộ 2 chiều)
#define NODE2_SSID         "ESP32-Node-2"
#define NODE2_PASSWORD     "12345678"
#define NODE2_IP           "192.168.5.1"
#define NODE2_HTTP_PORT    80

#define LED_PIN            2
#define BOOT_PIN           0
#define HTTP_PORT          80
#define AUDIO_PORT         8080
#define UPLOAD_PORT        8081      // Raw TCP upload — bypass WebServer body issue
#define SPIFFS_FILE        "/audio.wav"
#define MAX_FILE_SIZE      1800000   // 1.8 MB

#define MY_AP_IP_STR       "192.168.4.1"

// ── Sync interval ─────────────────────────────────────────────
#define SYNC_INTERVAL_MS   15000UL   // Node-1 đồng bộ mỗi 15 giây (lệch Node-2 để tránh deadlock)

WebServer  server(HTTP_PORT);
WiFiServer audioServer(AUDIO_PORT);
WiFiServer uploadServer(UPLOAD_PORT); // Raw TCP upload server

// ── RAM buffer (giữ audio.wav trong RAM để phục vụ nhanh) ─────
uint8_t* ramBuf   = nullptr;
size_t   ramSize  = 0;
bool     ramReady = false;

// ── State ─────────────────────────────────────────────────────
bool   nodeEnabled    = true;
bool   lastRawBoot    = HIGH;
bool   stableBoot     = HIGH;
unsigned long lastDebounceMs = 0;
#define DEBOUNCE_MS 50

// ── LED ───────────────────────────────────────────────────────
void blinkLED(int times, int ms = 100) {
  for (int i = 0; i < times; i++) {
    digitalWrite(LED_PIN, LOW);  delay(ms);
    digitalWrite(LED_PIN, HIGH); delay(ms);
  }
}

// ── Toggle node ON/OFF ────────────────────────────────────────
void enableNode() {
  nodeEnabled = true;
  WiFi.mode(WIFI_AP_STA);   // AP_STA để vừa phát AP vừa kết nối vào Node-2
  IPAddress apIP(192,168,4,1);
  IPAddress gw(192,168,4,1);
  IPAddress sn(255,255,255,0);
  WiFi.softAPConfig(apIP,gw,sn);
  WiFi.softAP(MY_AP_SSID,MY_AP_PASSWORD,MY_AP_CHANNEL,MY_AP_HIDDEN,MY_AP_MAX_CON);
  server.begin();
  audioServer.begin();
  uploadServer.begin();
  digitalWrite(LED_PIN, HIGH);
  Serial.println("[BOOT] Node-1 ENABLED — AP_STA ON");
  blinkLED(3, 80);
}

void disableNode() {
  nodeEnabled = false;
  WiFi.softAPdisconnect(true);
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  digitalWrite(LED_PIN, LOW);
  Serial.println("[BOOT] Node-1 DISABLED — AP OFF (low power)");
  blinkLED(2, 300);
  digitalWrite(LED_PIN, LOW);
}

void checkBootButton() {
  bool raw = digitalRead(BOOT_PIN);
  if (raw != lastRawBoot) { lastDebounceMs = millis(); lastRawBoot = raw; }
  if ((millis() - lastDebounceMs) >= DEBOUNCE_MS) {
    if (raw != stableBoot) {
      if (stableBoot == HIGH && raw == LOW) {
        if (nodeEnabled) disableNode();
        else             enableNode();
      }
      stableBoot = raw;
    }
  }
}

// ── MIME type lookup ──────────────────────────────────────────
String mimeForExt(const String& ext) {
  // ext phải lowercase, có dấu chấm, ví dụ ".wav"
  if (ext == ".wav")  return "audio/wav";
  if (ext == ".mp3")  return "audio/mpeg";
  if (ext == ".ogg")  return "audio/ogg";
  if (ext == ".flac") return "audio/flac";
  if (ext == ".aac")  return "audio/aac";
  if (ext == ".png")  return "image/png";
  if (ext == ".jpg" || ext == ".jpeg") return "image/jpeg";
  if (ext == ".gif")  return "image/gif";
  if (ext == ".bmp")  return "image/bmp";
  if (ext == ".webp") return "image/webp";
  if (ext == ".svg")  return "image/svg+xml";
  if (ext == ".pdf")  return "application/pdf";
  if (ext == ".txt")  return "text/plain";
  if (ext == ".csv")  return "text/csv";
  if (ext == ".json") return "application/json";
  if (ext == ".xml")  return "application/xml";
  if (ext == ".zip")  return "application/zip";
  if (ext == ".docx") return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  if (ext == ".xlsx") return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  if (ext == ".bin")  return "application/octet-stream";
  return "application/octet-stream";
}

// ── Sanitize filename — giữ nguyên extension gốc ─────────────
// Nếu extension KHÔNG có trong danh sách an toàn → vẫn giữ,
// chỉ strip các ký tự nguy hiểm khỏi base name.
String sanitizeFilename(const String& nameIn) {
  String name = nameIn;
  name.trim();
  if (name.length() == 0) return "";

  // Tách base và extension (dùng dấu chấm cuối cùng)
  int dotIdx = name.lastIndexOf('.');
  String base = (dotIdx > 0) ? name.substring(0, dotIdx) : name;
  String ext  = (dotIdx > 0) ? name.substring(dotIdx)    : "";

  // lowercase extension để so sánh MIME
  String extLow = ext; extLow.toLowerCase();

  // Sanitize base: giữ alphanumeric, '-', '_', khoảng trắng→'_'
  // Tối đa 32 ký tự để SPIFFS không bị giới hạn path
  String outBase = "";
  bool lastUnderscore = false;
  for (int i = 0; i < (int)base.length() && (int)outBase.length() < 32; i++) {
    char c = base[i];
    if (isAlphaNumeric(c) || c == '-') {
      outBase += c;
      lastUnderscore = false;
    } else if (c == '_' || c == ' ' || c == '.' || c == '(' || c == ')') {
      // Thay khoảng trắng và ký tự phổ biến bằng '_', không lặp
      if (!lastUnderscore && outBase.length() > 0) {
        outBase += '_';
        lastUnderscore = true;
      }
    }
    // Bỏ qua ký tự khác
  }
  // Xóa '_' cuối
  while (outBase.length() > 0 && outBase[outBase.length()-1] == '_')
    outBase.remove(outBase.length()-1);
  if (outBase.length() == 0) return "";

  // Sanitize extension: chỉ giữ alphanumeric (tối đa 8 ký tự sau dấu chấm)
  String outExt = "";
  if (extLow.length() > 1) {
    outExt = ".";
    for (int i = 1; i < (int)extLow.length() && (int)outExt.length() < 9; i++) {
      char c = extLow[i];
      if (isAlphaNumeric(c)) outExt += c;
    }
    if (outExt.length() <= 1) outExt = "";
  }
  if (outExt.length() == 0) outExt = ".bin";

  return outBase + outExt;
}

static uint16_t _fileCounter = 0;
String genAutoFilename() {
  _fileCounter++;
  char buf[24];
  snprintf(buf, sizeof(buf), "file_%04d.bin", _fileCounter);
  return String(buf);
}

// ── SPIFFS helpers ────────────────────────────────────────────
bool spiffsHasFile() { return SPIFFS.exists(SPIFFS_FILE); }

size_t spiffsFileSize() {
  if (!spiffsHasFile()) return 0;
  File f = SPIFFS.open(SPIFFS_FILE,"r");
  if (!f) return 0;
  size_t sz = f.size(); f.close(); return sz;
}

bool spiffsSaveAs(const uint8_t* buf, size_t size, const String& path) {
  size_t freeBytes = SPIFFS.totalBytes() - SPIFFS.usedBytes();
  if (size > freeBytes) {
    Serial.printf("[SPIFFS] Not enough space: need %d, free %d bytes\n", size, freeBytes);
    return false;
  }
  File f = SPIFFS.open(path, "w");
  if (!f) { Serial.printf("[SPIFFS] Open '%s' FAILED\n", path.c_str()); return false; }
  size_t wr = f.write(buf, size); f.close();
  bool ok = (wr == size);
  if (!ok) {
    SPIFFS.remove(path);
    Serial.printf("[SPIFFS] SaveAs '%s' FAILED (%d/%d)\n", path.c_str(), wr, size);
  } else {
    Serial.printf("[SPIFFS] SaveAs '%s' %d/%d → OK\n", path.c_str(), wr, size);
  }
  return ok;
}

bool spiffsLoad() {
  if (!spiffsHasFile()) return false;
  File f = SPIFFS.open(SPIFFS_FILE,"r");
  if (!f) return false;
  size_t sz = f.size();
  if (sz == 0 || sz > MAX_FILE_SIZE) { f.close(); return false; }
  if (ramBuf) { free(ramBuf); ramBuf = nullptr; ramSize = 0; }
  ramBuf = (uint8_t*)malloc(sz);
  if (!ramBuf) { f.close(); Serial.println("[SPIFFS] OOM"); return false; }
  size_t rd = f.read(ramBuf, sz); f.close();
  ramSize = rd; ramReady = (rd >= 44);
  Serial.printf("[SPIFFS] Load %d bytes → %s\n", rd, ramReady?"OK":"FAIL");
  return ramReady;
}

// ── Helpers ───────────────────────────────────────────────────
String formatUptime(uint32_t ms) {
  uint32_t s=ms/1000, m=s/60; s%=60; uint32_t h=m/60; m%=60;
  char b[32]; snprintf(b,sizeof(b),"%02d:%02d:%02d",h,m,s); return String(b);
}

// Trả về WAV info JSON — chỉ gọi khi file là WAV
String wavInfoJson(const uint8_t* buf, size_t size) {
  if (!buf||size<44) return "{}";
  if (buf[0]!='R'||buf[1]!='I'||buf[2]!='F'||buf[3]!='F') return "{\"is_wav\":false}";
  if (buf[8]!='W'||buf[9]!='A'||buf[10]!='V'||buf[11]!='E') return "{\"is_wav\":false}";
  uint16_t fmt = buf[20]|(buf[21]<<8);
  uint16_t ch  = buf[22]|(buf[23]<<8);
  uint32_t sr  = buf[24]|(buf[25]<<8)|(buf[26]<<16)|(buf[27]<<24);
  uint16_t bps = buf[34]|(buf[35]<<8);
  uint32_t dsz = buf[40]|(buf[41]<<8)|(buf[42]<<16)|(buf[43]<<24);
  float dur = (sr>0&&ch>0&&bps>0) ? (float)dsz/(sr*ch*(bps/8)) : 0.0f;
  String j = "{\"is_wav\":true";
  j += ",\"format\":\""  + String(fmt==1?"PCM":fmt==3?"FLOAT":"OTHER") + "\"";
  j += ",\"channels\":"  + String(ch);
  j += ",\"sample_rate\":"     + String(sr);
  j += ",\"bits_per_sample\":" + String(bps);
  j += ",\"data_size\":"       + String(dsz);
  j += ",\"duration_sec\":"    + String(dur,2) + "}";
  return j;
}

// ── HTTP Handlers ─────────────────────────────────────────────

void handleStatus() {
  server.send(200,"application/json",
    String("{\"node\":1") +
    ",\"ap_ssid\":\""   + MY_AP_SSID   + "\"" +
    ",\"ap_ip\":\""     + MY_AP_IP_STR + "\"" +
    ",\"uptime\":\""    + formatUptime(millis()) + "\"" +
    ",\"free_heap\":"   + String(ESP.getFreeHeap()) +
    ",\"spiffs_has_file\":" + (spiffsHasFile()?"true":"false") +
    ",\"spiffs_total\":" + String(SPIFFS.totalBytes()) +
    ",\"spiffs_used\":"  + String(SPIFFS.usedBytes())  +
    ",\"spiffs_free\":"  + String(SPIFFS.totalBytes()-SPIFFS.usedBytes()) +
    ",\"ram_ready\":"   + (ramReady?"true":"false") +
    ",\"ram_size\":"    + String(ramSize) +
    ",\"node_enabled\":" + (nodeEnabled?"true":"false") +
    ",\"builtin_wav_size\":" + String(TEST_WAV_SIZE) + "}");
}

void handleFileInfo() {
  bool has = spiffsHasFile(); size_t sz = spiffsFileSize();
  String j = "{\"has_file\":" + String(has?"true":"false");
  j += ",\"path\":\"" + String(SPIFFS_FILE) + "\"";
  j += ",\"size\":"   + String(sz);
  j += ",\"size_kb\":" + String(sz/1024.0f,1);
  if (has && ramReady && ramBuf) j += ",\"wav_info\":" + wavInfoJson(ramBuf,ramSize);
  j += ",\"free_heap\":" + String(ESP.getFreeHeap()) + "}";
  server.send(200,"application/json",j);
}

// GET /file/list — liệt kê TẤT CẢ file trong SPIFFS
void handleFileList() {
  std::vector<String> names;
  {
    File root = SPIFFS.open("/");
    File fi   = root.openNextFile();
    while (fi) {
      if (!fi.isDirectory()) names.push_back(String(fi.name()));
      fi.close();
      fi = root.openNextFile();
    }
    root.close();
  }
  String j = "{\"files\":[";
  int count = 0;
  for (auto& fname : names) {
    String path        = fname.startsWith("/") ? fname : ("/" + fname);
    String displayName = fname.startsWith("/") ? fname.substring(1) : fname;
    File f2 = SPIFFS.open(path, "r");
    size_t sz = f2 ? f2.size() : 0;

    // Kiểm tra xem có phải WAV không (chỉ parse WAV info cho file .wav)
    String extChk = displayName;
    extChk.toLowerCase();
    bool isWav = extChk.endsWith(".wav");
    float dur = 0.0f;
    if (isWav && sz >= 44) {
      if (ramReady && ramBuf && ramSize >= 44 && path == String(SPIFFS_FILE)) {
        uint16_t ch  = ramBuf[22]|(ramBuf[23]<<8);
        uint32_t sr  = ramBuf[24]|(ramBuf[25]<<8)|(ramBuf[26]<<16)|(ramBuf[27]<<24);
        uint16_t bps = ramBuf[34]|(ramBuf[35]<<8);
        uint32_t dsz = ramBuf[40]|(ramBuf[41]<<8)|(ramBuf[42]<<16)|(ramBuf[43]<<24);
        if (sr>0&&ch>0&&bps>0) dur = (float)dsz/(sr*ch*(bps/8));
      } else if (f2) {
        uint8_t hdr[44]; f2.seek(0); f2.read(hdr,44);
        uint16_t ch  = hdr[22]|(hdr[23]<<8);
        uint32_t sr  = hdr[24]|(hdr[25]<<8)|(hdr[26]<<16)|(hdr[27]<<24);
        uint16_t bps = hdr[34]|(hdr[35]<<8);
        uint32_t dsz = hdr[40]|(hdr[41]<<8)|(hdr[42]<<16)|(hdr[43]<<24);
        if (sr>0&&ch>0&&bps>0) dur = (float)dsz/(sr*ch*(bps/8));
      }
    }
    if (f2) f2.close();

    // MIME type
    int di = displayName.lastIndexOf('.');
    String extStr = (di >= 0) ? displayName.substring(di) : "";
    extStr.toLowerCase();
    String mime = mimeForExt(extStr);

    if (count > 0) j += ",";
    char sz_kb[16]; snprintf(sz_kb, sizeof(sz_kb), "%.1f KB", sz/1024.0f);
    j += "{\"name\":\""   + displayName + "\"";
    j += ",\"path\":\""   + path        + "\"";
    j += ",\"size\":"     + String(sz);
    j += ",\"size_kb\":\"" + String(sz_kb) + "\"";
    j += ",\"mime\":\""   + mime         + "\"";
    if (isWav) j += ",\"duration_sec\":" + String(dur,2);
    j += "}";
    count++;
  }
  j += "],\"count\":"         + String(count);
  j += ",\"spiffs_total\":"   + String(SPIFFS.totalBytes());
  j += ",\"spiffs_used\":"    + String(SPIFFS.usedBytes());
  j += ",\"spiffs_free\":"    + String(SPIFFS.totalBytes()-SPIFFS.usedBytes()) + "}";
  server.send(200,"application/json",j);
}

// GET /file/download?name=<filename> — download bất kỳ file nào
// Nếu không có ?name= → trả về audio.wav (tương thích cũ)
// Dùng raw client.write() để tránh chunked Transfer-Encoding của WebServer
void handleFileDownload() {
  String name = server.arg("name");
  name.trim();

  String filePath;
  String dlName;

  if (name.length() == 0) {
    filePath = SPIFFS_FILE;
    dlName   = "audio.wav";
  } else {
    // Thử path gốc trước (không sanitize) để khớp tên đã lưu
    String pathRaw = name.startsWith("/") ? name : ("/" + name);
    if (SPIFFS.exists(pathRaw)) {
      filePath = pathRaw;
      dlName   = name.startsWith("/") ? name.substring(1) : name;
    } else {
      // Fallback: sanitize
      String safe = sanitizeFilename(name);
      if (safe.length() == 0) {
        server.send(400,"application/json","{\"error\":\"invalid filename\"}");
        return;
      }
      filePath = "/" + safe;
      dlName   = safe;
    }
  }

  // Xác định MIME type từ extension
  int di = dlName.lastIndexOf('.');
  String ext = (di >= 0) ? dlName.substring(di) : "";
  ext.toLowerCase();
  String mime = mimeForExt(ext);

  if (SPIFFS.exists(filePath)) {
    File f = SPIFFS.open(filePath,"r");
    if (f) {
      size_t sz = f.size();
      // Ghi HTTP response trực tiếp qua raw client để tránh chunked encoding
      WiFiClient cli = server.client();
      cli.printf("HTTP/1.1 200 OK\r\n"
                 "Content-Type: %s\r\n"
                 "Content-Length: %d\r\n"
                 "Content-Disposition: attachment; filename=\"%s\"\r\n"
                 "Connection: close\r\n\r\n",
                 mime.c_str(), sz, dlName.c_str());
      uint8_t buf[1024]; size_t sent = 0;
      while (sent < sz && cli.connected()) {
        size_t rd = f.read(buf, min((size_t)1024, sz - sent));
        if (rd == 0) break;
        cli.write(buf, rd);
        sent += rd;
      }
      cli.flush();
      f.close();
      Serial.printf("[Download] '%s' %d/%d bytes  MIME=%s\n",
                    dlName.c_str(), sent, sz, mime.c_str());
      blinkLED(3,100); return;
    }
  }

  // Fallback: audio.wav từ RAM hoặc builtin
  if (name.length() == 0 || dlName == "audio.wav") {
    const uint8_t* src  = nullptr;
    size_t         srcSz = 0;
    if (ramReady && ramSize > 0) { src = ramBuf;       srcSz = ramSize; }
    else if (TEST_WAV_SIZE > 0)  { src = TEST_WAV_DATA; srcSz = TEST_WAV_SIZE; }
    if (src && srcSz > 0) {
      WiFiClient cli = server.client();
      cli.printf("HTTP/1.1 200 OK\r\n"
                 "Content-Type: audio/wav\r\n"
                 "Content-Length: %d\r\n"
                 "Content-Disposition: attachment; filename=\"audio.wav\"\r\n"
                 "Connection: close\r\n\r\n", srcSz);
      size_t sent = 0;
      while (sent < srcSz && cli.connected()) {
        size_t ch = min((size_t)1024, srcSz - sent);
        cli.write(src + sent, ch);
        sent += ch;
      }
      cli.flush();
      blinkLED(3,100); return;
    }
  }

  server.send(404,"application/json","{\"error\":\"file not found\",\"name\":\"" + dlName + "\"}");
}

// POST /file/upload — upload file bất kỳ định dạng (binary-safe)
// Python gửi raw binary body (Content-Type: octet-stream / MIME riêng).
// WebServer KHÔNG buffer body cho non-form content-type → server.client()
// vẫn còn bytes để đọc khi handler được gọi.
// Header: X-Filename: <tên file gốc, ví dụ photo.png>
void handleFileUpload() {
  String xFilename = server.header("X-Filename");
  if (xFilename.length() == 0) xFilename = server.arg("name");
  xFilename.trim();

  String saveAs = sanitizeFilename(xFilename);
  if (saveAs.length() == 0) saveAs = genAutoFilename();

  int clen = 0;
  String clHeader = server.header("Content-Length");
  if (clHeader.length() > 0) clen = clHeader.toInt();

  if (clen <= 0) {
    server.send(400,"application/json","{\"error\":\"missing Content-Length\"}");
    return;
  }
  if (clen > (int)MAX_FILE_SIZE) {
    server.send(413,"application/json","{\"error\":\"file too large\"}");
    return;
  }

  // Mở file SPIFFS để ghi stream (không cần buffer toàn bộ trong RAM)
  String path = "/" + saveAs;
  if (SPIFFS.exists(path)) SPIFFS.remove(path);
  File spiffsFile = SPIFFS.open(path, "w");
  if (!spiffsFile) {
    server.send(500,"application/json","{\"error\":\"spiffs open failed\"}");
    return;
  }

  // Đọc từ raw TCP client → ghi thẳng vào SPIFFS theo từng chunk
  // Lưu ý: WebServer có thể đã đọc một phần body vào internal buffer.
  // Dùng server.arg("plain") để lấy phần đã buffer, sau đó đọc thêm từ client.
  WiFiClient cli = server.client();

  // Đợi tối đa 2s để TCP buffer fill (WiFi có thể chậm hơn handler)
  unsigned long tWait = millis();
  while (cli.available() == 0 && cli.connected() && (millis()-tWait) < 2000) {
    delay(5);
  }

  size_t rx = 0;
  uint8_t chunk[512];
  unsigned long t = millis();

  // Kiểm tra nếu WebServer đã buffer body qua server.arg("plain")
  String plainBody = server.arg("plain");
  if (plainBody.length() > 0 && (int)plainBody.length() <= clen) {
    size_t pbLen = plainBody.length();
    spiffsFile.write((const uint8_t*)plainBody.c_str(), pbLen);
    rx = pbLen;
    Serial.printf("[Upload] plain body: %d bytes pre-buffered\n", pbLen);
    t = millis();
  }

  // Đọc phần còn lại từ TCP
  while (rx < (size_t)clen && cli.connected() && (millis()-t) < 30000) {
    size_t av = cli.available();
    if (av > 0) {
      size_t want = min(av, min((size_t)512, (size_t)(clen - rx)));
      size_t rd   = cli.readBytes(chunk, want);
      if (rd > 0) {
        spiffsFile.write(chunk, rd);
        rx += rd;
        t = millis();
      }
    } else {
      delay(2);
    }
  }
  spiffsFile.close();

  Serial.printf("[Upload] '%s' rx=%d/%d bytes\n", saveAs.c_str(), rx, clen);

  bool saved = (rx > 0 && SPIFFS.exists(path));
  if (saved) {
    File chk = SPIFFS.open(path, "r");
    if (chk) { saved = (chk.size() == rx); chk.close(); }
  }
  if (!saved) SPIFFS.remove(path);

  // Nếu là audio.wav → load vào RAM
  if (saved && saveAs == "audio.wav") {
    if (ramBuf) { free(ramBuf); ramBuf = nullptr; ramSize = 0; ramReady = false; }
    File f = SPIFFS.open(path, "r");
    if (f) {
      size_t sz = f.size();
      ramBuf = (uint8_t*)malloc(sz);
      if (ramBuf) { size_t rd=f.read(ramBuf,sz); f.close(); ramSize=rd; ramReady=(rd>=44); }
      else f.close();
    }
  }

  Serial.printf("[Upload] '%s' %d bytes → %s\n", saveAs.c_str(), rx, saved?"OK":"FAIL");
  blinkLED(saved?5:2, 80);

  String resp = "{\"status\":\"" + String(saved?"ok":"fail") + "\""
    ",\"filename\":\"" + saveAs + "\""
    ",\"size\":"       + String(rx) +
    ",\"spiffs_saved\":" + String(saved?"true":"false") + "}";
  server.send(saved?200:500, "application/json", resp);
}

void handleFileClear() {
  // Xóa audio.wav (file chính)
  bool ok = SPIFFS.remove(SPIFFS_FILE);
  if (ramBuf) { free(ramBuf); ramBuf=nullptr; ramSize=0; ramReady=false; }
  server.send(200,"application/json",
    ok ? "{\"status\":\"ok\",\"message\":\"File da xoa\"}"
       : "{\"status\":\"ok\",\"message\":\"Khong co file de xoa\"}");
}

// POST /file/delete?name=<filename>
// Thử path gốc trước (không sanitize), fallback sang sanitized name
void handleFileDelete() {
  String name = server.arg("name"); name.trim();
  if (name.length() == 0) {
    server.send(400,"application/json","{\"error\":\"missing name\"}"); return;
  }

  // Thử path trực tiếp trước (giữ nguyên tên, chỉ thêm '/')
  String pathRaw = name.startsWith("/") ? name : ("/" + name);
  String path = "";
  if (SPIFFS.exists(pathRaw)) {
    path = pathRaw;
  } else {
    // Fallback: sanitize rồi thử lại
    String safe = sanitizeFilename(name);
    if (safe.length() > 0) {
      String pathSafe = "/" + safe;
      if (SPIFFS.exists(pathSafe)) path = pathSafe;
    }
  }

  if (path.length() == 0) {
    Serial.printf("[SPIFFS] Delete '%s' — not found (raw=%s)\n", name.c_str(), pathRaw.c_str());
    server.send(404,"application/json","{\"error\":\"file not found\"}"); return;
  }

  bool ok = SPIFFS.remove(path);
  if (ok && path == String(SPIFFS_FILE)) {
    if (ramBuf) { free(ramBuf); ramBuf=nullptr; ramSize=0; ramReady=false; }
  }
  server.send(ok?200:500,"application/json",
    ok?"{\"status\":\"ok\"}":"{\"error\":\"delete failed\"}");
  Serial.printf("[SPIFFS] Delete '%s' → %s\n", path.c_str(), ok?"OK":"FAIL");
}

void handleRamInfo() {
  if (!ramReady || ramSize < 44) {
    server.send(200,"application/json",
      String("{\"ram_ready\":false,\"free_heap\":") + String(ESP.getFreeHeap()) +
      ",\"spiffs_has_file\":" + String(spiffsHasFile()?"true":"false") + "}");
    return;
  }
  char magic[5]={0}; memcpy(magic,ramBuf,4);
  String j = "{\"ram_ready\":true,\"size_bytes\":" + String(ramSize);
  j += ",\"magic\":\""    + String(magic) + "\"";
  j += ",\"wav_info\":"   + wavInfoJson(ramBuf,ramSize);
  j += ",\"free_heap\":"  + String(ESP.getFreeHeap()) + "}";
  server.send(200,"application/json",j);
}

// ── Busy flag — Node-2 dùng để kiểm tra trước khi kết nối ───────────────
static bool _node1SyncBusy = false;  // true khi Node-1 đang là STA (đồng bộ từ Node-2)

// GET /sync/busy — trả về {"busy": true/false}
void handleSyncBusy() {
  server.send(200, "application/json",
    String("{\"busy\":") + (_node1SyncBusy ? "true" : "false") +
    ",\"node\":1}");
}

// GET /sync/status — Node-2 gọi để kiểm tra trước khi đồng bộ
void handleSyncStatus() {
  // Đếm số file trong SPIFFS
  int count = 0;
  std::vector<String> names;
  {
    File root = SPIFFS.open("/");
    File fi   = root.openNextFile();
    while (fi) {
      if (!fi.isDirectory()) {
        names.push_back(String(fi.name()));
        count++;
      }
      fi.close();
      fi = root.openNextFile();
    }
    root.close();
  }
  String j = "{\"node\":1";
  j += ",\"file_count\":"   + String(count);
  j += ",\"spiffs_used\":"  + String(SPIFFS.usedBytes());
  j += ",\"spiffs_free\":"  + String(SPIFFS.totalBytes()-SPIFFS.usedBytes());
  j += ",\"uptime\":\""     + formatUptime(millis()) + "\"";
  j += ",\"free_heap\":"    + String(ESP.getFreeHeap());
  j += ",\"files\":[";
  for (int i = 0; i < (int)names.size(); i++) {
    String path        = names[i].startsWith("/") ? names[i] : ("/" + names[i]);
    String displayName = names[i].startsWith("/") ? names[i].substring(1) : names[i];
    File f = SPIFFS.open(path, "r");
    size_t sz = f ? f.size() : 0;
    if (f) f.close();
    if (i > 0) j += ",";
    j += "{\"name\":\"" + displayName + "\",\"size\":" + String(sz) + "}";
  }
  j += "]}";
  server.send(200,"application/json",j);
  Serial.printf("[Sync/Status] Thiết bị B hỏi — Danh sách: %d file\n", count);
}

// ── Raw TCP Upload Server (port 8081) ─────────────────────────
// Bypass WebServer library hoàn toàn — đọc HTTP request + body trực tiếp
void handleRawUpload(WiFiClient& cli) {
  // Đọc request line
  String reqLine = cli.readStringUntil('\n'); reqLine.trim();
  Serial.printf("[Upload8081] %s\n", reqLine.substring(0,60).c_str());

  // Đọc headers
  String xFilename = "";
  int    clen      = 0;
  unsigned long th = millis();
  while (cli.connected() && (millis()-th) < 5000) {
    if (!cli.available()) { delay(2); continue; }
    String line = cli.readStringUntil('\n'); line.trim();
    if (line.length() == 0) break;  // blank line = end of headers
    String lo = line; lo.toLowerCase();
    if (lo.startsWith("content-length:")) {
      String val = line.substring(line.indexOf(':')+1); val.trim();
      clen = val.toInt();
    }
    if (lo.startsWith("x-filename:")) {
      xFilename = line.substring(line.indexOf(':')+1); xFilename.trim();
    }
    th = millis();
  }
  Serial.printf("[Upload8081] fname='%s' CL=%d\n", xFilename.c_str(), clen);

  if (clen <= 0 || clen > (int)MAX_FILE_SIZE) {
    cli.print("HTTP/1.0 400 Bad Request\r\nContent-Length: 20\r\nConnection: close\r\n\r\n{\"error\":\"bad clen\"}");
    cli.flush(); return;
  }

  String saveAs = sanitizeFilename(xFilename);
  if (saveAs.length() == 0) saveAs = genAutoFilename();
  String path = "/" + saveAs;

  if (SPIFFS.exists(path)) SPIFFS.remove(path);
  File spiffsFile = SPIFFS.open(path, "w");
  if (!spiffsFile) {
    cli.print("HTTP/1.0 500 Internal Server Error\r\nContent-Length: 27\r\nConnection: close\r\n\r\n{\"error\":\"spiffs open failed\"}");
    cli.flush(); return;
  }

  // Đọc body trực tiếp từ TCP — không qua WebServer
  size_t rx = 0;
  uint8_t chunk[512];
  unsigned long t = millis();
  while (rx < (size_t)clen && cli.connected() && (millis()-t) < 30000) {
    size_t av = cli.available();
    if (av > 0) {
      size_t want = min(av, min((size_t)512, (size_t)(clen-rx)));
      size_t rd   = cli.readBytes(chunk, want);
      if (rd > 0) { spiffsFile.write(chunk, rd); rx += rd; t = millis(); }
    } else { delay(2); }
  }
  spiffsFile.close();
  Serial.printf("[Upload8081] '%s' rx=%d/%d\n", saveAs.c_str(), rx, clen);

  bool saved = (rx > 0 && SPIFFS.exists(path));
  if (saved) { File chk=SPIFFS.open(path,"r"); if(chk){saved=(chk.size()==rx);chk.close();} }
  if (!saved) SPIFFS.remove(path);

  // Nếu là audio.wav → load vào RAM
  if (saved && saveAs == "audio.wav") {
    if (ramBuf) { free(ramBuf); ramBuf=nullptr; ramSize=0; ramReady=false; }
    File f = SPIFFS.open(path,"r");
    if (f) { size_t sz=f.size(); ramBuf=(uint8_t*)malloc(sz);
      if (ramBuf){size_t rd=f.read(ramBuf,sz);f.close();ramSize=rd;ramReady=(rd>=44);}
      else f.close(); }
  }

  blinkLED(saved?5:2, 80);
  Serial.printf("[Upload8081] '%s' %d bytes → %s\n", saveAs.c_str(), rx, saved?"OK":"FAIL");

  String resp = "{\"status\":\"" + String(saved?"ok":"fail") + "\""
    ",\"filename\":\"" + saveAs + "\""
    ",\"size\":"       + String(rx) +
    ",\"spiffs_saved\":" + String(saved?"true":"false") + "}";
  cli.printf("HTTP/1.0 %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s",
             saved?"200 OK":"500 Internal Server Error",
             (int)resp.length(), resp.c_str());
  cli.flush();
}

void handleNotFound() {
  server.send(404,"application/json","{\"error\":\"not found\"}");
}

// ── Raw TCP port 8080 (tương thích firmware cũ) ───────────────
void handleRawTCP(WiFiClient& client) {
  String req = client.readStringUntil('\n'); req.trim();
  int clen = 0;
  String xFilename = "";
  while (client.connected()) {
    String line = client.readStringUntil('\n'); line.trim();
    if (line.length() == 0) break;
    String lo = line; lo.toLowerCase();
    if (lo.startsWith("content-length:"))
      clen = line.substring(line.indexOf(':')+1).toInt();
    if (lo.startsWith("x-filename:")) {
      xFilename = line.substring(line.indexOf(':')+1); xFilename.trim();
    }
  }

  if (req.startsWith("GET")) {
    // Phục vụ audio.wav từ SPIFFS/RAM/builtin
    if (spiffsHasFile()) {
      File f = SPIFFS.open(SPIFFS_FILE,"r");
      if (f) {
        size_t sz = f.size();
        client.printf("HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\nContent-Length: %d\r\n"
                      "Content-Disposition: attachment; filename=\"audio.wav\"\r\nConnection: close\r\n\r\n",sz);
        uint8_t buf[1024]; size_t sent=0;
        while(sent<sz&&client.connected()){size_t rd=f.read(buf,min((size_t)1024,sz-sent));client.write(buf,rd);sent+=rd;}
        f.close(); client.flush(); blinkLED(3,100); return;
      }
    }
    const uint8_t* buf = (ramReady&&ramSize>0) ? ramBuf : TEST_WAV_DATA;
    size_t sz          = (ramReady&&ramSize>0) ? ramSize : TEST_WAV_SIZE;
    if (sz == 0) { client.print("HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n"); return; }
    client.printf("HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\nContent-Length: %d\r\n"
                  "Content-Disposition: attachment; filename=\"audio.wav\"\r\nConnection: close\r\n\r\n",sz);
    size_t sent=0;
    while(sent<sz&&client.connected()){size_t ch=min((size_t)1024,sz-sent);client.write(buf+sent,ch);sent+=ch;}
    client.flush(); blinkLED(3,100);

  } else if (req.startsWith("POST")) {
    if (clen <= 0 || clen > (int)MAX_FILE_SIZE) {
      client.print("HTTP/1.1 400\r\nConnection: close\r\n\r\n"); return;
    }
    uint8_t* buf = (uint8_t*)malloc(clen);
    if (!buf) { client.print("HTTP/1.1 507\r\nConnection: close\r\n\r\n"); return; }
    size_t rx=0; unsigned long t=millis();
    while(rx<(size_t)clen&&client.connected()&&(millis()-t)<20000){
      size_t av=client.available();
      if(av>0){size_t ch=min(av,(size_t)(clen-rx));client.readBytes(buf+rx,ch);rx+=ch;t=millis();}else delay(1);
    }
    if (rx > 0) {
      String saveAs = sanitizeFilename(xFilename);
      if (saveAs.length() == 0) saveAs = genAutoFilename();
      bool sv = spiffsSaveAs(buf, rx, "/" + saveAs);
      if (sv && saveAs == "audio.wav") {
        if (ramBuf){free(ramBuf);ramBuf=nullptr;ramSize=0;}
        ramBuf=buf; ramSize=rx; ramReady=true; buf=nullptr; // buf ownership transferred
      }
      if (buf) free(buf);
      String r = "{\"status\":\"ok\",\"received\":" + String(rx) +
                 ",\"filename\":\"" + saveAs + "\""
                 ",\"spiffs_saved\":" + String(sv?"true":"false") + "}";
      client.printf("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s",
                    r.length(), r.c_str());
      blinkLED(5,80);
    } else {
      free(buf);
      client.print("HTTP/1.1 400\r\nConnection: close\r\n\r\n{\"error\":\"incomplete\"}");
    }
  }
}

// ── Setup ─────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200); delay(500);
  pinMode(LED_PIN, OUTPUT); digitalWrite(LED_PIN, LOW);
  pinMode(BOOT_PIN, INPUT_PULLUP);

  Serial.println("\n══════════════════════════════");
  Serial.println(" ESP32 NODE-1  (AP + SPIFFS Server)");
  Serial.println("══════════════════════════════");

  // Init SPIFFS
  if (!SPIFFS.begin(true)) { SPIFFS.format(); SPIFFS.begin(true); }
  Serial.printf("[SPIFFS] Total:%d Used:%d bytes\n",
    SPIFFS.totalBytes(), SPIFFS.usedBytes());

  // Bật WiFi AP_STA (AP + STA để đồng bộ 2 chiều)
  WiFi.mode(WIFI_AP_STA);
  IPAddress apIP(192,168,4,1);
  IPAddress gw(192,168,4,1);
  IPAddress sn(255,255,255,0);
  WiFi.softAPConfig(apIP,gw,sn);
  WiFi.softAP(MY_AP_SSID,MY_AP_PASSWORD,MY_AP_CHANNEL,MY_AP_HIDDEN,MY_AP_MAX_CON);
  delay(200);
  Serial.printf("[AP] SSID: %s  IP: %s\n", MY_AP_SSID, WiFi.softAPIP().toString().c_str());
  digitalWrite(LED_PIN, HIGH);

  // Đăng ký collect headers để handleFileUpload đọc được X-Filename
  const char* collectHeaders[] = {"X-Filename","Content-Length","Content-Type"};
  server.collectHeaders(collectHeaders, 3);

  // Đăng ký routes
  server.on("/status",        HTTP_GET,  handleStatus);
  server.on("/file/info",     HTTP_GET,  handleFileInfo);
  server.on("/file/list",     HTTP_GET,  handleFileList);
  server.on("/file/download", HTTP_GET,  handleFileDownload);
  server.on("/file/upload",   HTTP_POST, handleFileUpload);
  server.on("/file/clear",    HTTP_POST, handleFileClear);
  server.on("/file/delete",   HTTP_POST, handleFileDelete);
  server.on("/ram/info",      HTTP_GET,  handleRamInfo);
  server.on("/sync/status",   HTTP_GET,  handleSyncStatus);   // Node-2 poll
  server.on("/sync/busy",     HTTP_GET,  handleSyncBusy);     // Node-2 kiểm tra trước khi kết nối
  // Tương thích firmware cũ
  server.on("/audio/info",    HTTP_GET,  handleFileInfo);
  server.on("/ram/clear",     HTTP_POST, handleFileClear);
  server.onNotFound(handleNotFound);
  server.begin();
  audioServer.begin();
  uploadServer.begin();

  // Nếu SPIFFS đã có file → load WAV vào RAM
  if (spiffsHasFile()) {
    Serial.printf("[SPIFFS] Tìm thấy audio.wav: %d bytes\n", spiffsFileSize());
    spiffsLoad();
    if (ramReady) Serial.println("[SPIFFS] WAV đã tải vào RAM");
  }

  Serial.println("\n[Sẵn sàng] Node-1 (Thiết bị A) — Endpoints:");
  Serial.printf("  Kết nối WiFi: %s / %s\n",       MY_AP_SSID, MY_AP_PASSWORD);
  Serial.printf("  GET  http://%s/status\n",        MY_AP_IP_STR);
  Serial.printf("  GET  http://%s/file/list\n",     MY_AP_IP_STR);
  Serial.printf("  GET  http://%s/sync/status\n",   MY_AP_IP_STR);
  Serial.printf("  GET  http://%s/file/download?name=photo.png\n", MY_AP_IP_STR);
  Serial.printf("  POST http://%s/file/upload  (X-Filename: myfile.txt)\n", MY_AP_IP_STR);
  Serial.printf("  GET  http://%s:8080/        (TCP WAV)\n", MY_AP_IP_STR);
  Serial.printf("  [Tự động đồng bộ 2 chiều mỗi %d giây]\n", SYNC_INTERVAL_MS/1000);
  Serial.println("[BOOT] Nhấn nút BOOT để bật/tắt WiFi AP");
}

// ── Đếm số file SPIFFS ────────────────────────────────────────
int countSpiffsFiles() {
  int n = 0;
  File root = SPIFFS.open("/");
  File fi   = root.openNextFile();
  while (fi) {
    if (!fi.isDirectory()) n++;
    fi.close();
    fi = root.openNextFile();
  }
  root.close();
  return n;
}

// ── Helper: HTTP GET text từ Node-2 ──────────────────────────
String httpGetFromNode2(const char* path, int timeoutMs = 6000) {
  WiFiClient c;
  if (!c.connect(NODE2_IP, NODE2_HTTP_PORT)) return "";
  c.printf("GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n", path, NODE2_IP);
  int contentLength = -1;
  unsigned long t = millis();
  while (c.connected() && (millis()-t) < (unsigned long)timeoutMs) {
    if (!c.available()) { delay(1); continue; }
    String line = c.readStringUntil('\n'); line.trim();
    if (line.length() == 0) break;
    String lo = line; lo.toLowerCase();
    if (lo.startsWith("content-length:")) {
      String val = line.substring(line.indexOf(':')+1); val.trim();
      contentLength = val.toInt();
    }
    t = millis();
  }
  String body = "";
  uint8_t buf[256];
  if (contentLength > 0) body.reserve(contentLength + 4);
  t = millis();
  while (c.connected() && (millis()-t) < (unsigned long)timeoutMs) {
    size_t av = c.available();
    if (av > 0) {
      size_t rd = c.read(buf, min(av,(size_t)256));
      for (size_t i = 0; i < rd; i++) body += (char)buf[i];
      t = millis();
      if (contentLength > 0 && (int)body.length() >= contentLength) break;
    } else { delay(1); }
  }
  c.stop();
  return body;
}

// ── Helper: download 1 file từ Node-2 → lưu SPIFFS ───────────
bool httpDownloadFileFromNode2(const String& filename) {
  String path = "/file/download?name=" + filename;
  WiFiClient c;
  if (!c.connect(NODE2_IP, NODE2_HTTP_PORT)) {
    Serial.printf("[Sync2] HTTP connect FAILED for '%s'\n", filename.c_str());
    return false;
  }
  c.printf("GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n",
           path.c_str(), NODE2_IP);
  int contentLength = 0;
  unsigned long t = millis();
  while (c.connected() && (millis()-t) < 8000) {
    if (!c.available()) { delay(1); continue; }
    String line = c.readStringUntil('\n'); line.trim();
    if (line.length() == 0) break;
    String lo = line; lo.toLowerCase();
    if (lo.startsWith("content-length:"))
      contentLength = line.substring(line.indexOf(':')+1).toInt();
    if (lo.startsWith("http/") && lo.indexOf(" 200 ") < 0 && lo.indexOf(" 200\r") < 0) {
      c.stop(); Serial.printf("[Sync2] Non-200 for '%s'\n", filename.c_str()); return false;
    }
    t = millis();
  }
  if (contentLength <= 0 || contentLength > (int)MAX_FILE_SIZE) {
    c.stop();
    Serial.printf("[Sync2] Bad CL=%d for '%s'\n", contentLength, filename.c_str());
    return false;
  }
  uint8_t* buf = (uint8_t*)malloc(contentLength);
  if (!buf) { c.stop(); Serial.println("[Sync2] OOM"); return false; }
  size_t rx = 0; t = millis();
  while (rx < (size_t)contentLength && c.connected() && (millis()-t) < 30000) {
    size_t av = c.available();
    if (av > 0) {
      size_t want = min(min(av, (size_t)(contentLength-rx)), (size_t)4096);
      size_t rd = c.readBytes(buf+rx, want);
      rx += rd; t = millis();
    } else { delay(1); }
  }
  c.stop();
  Serial.printf("[Sync2] '%s' rx=%d/%d bytes\n", filename.c_str(), rx, contentLength);
  if (rx == 0) { free(buf); return false; }
  bool saved = spiffsSaveAs(buf, rx, "/" + filename);
  if (saved && filename == "audio.wav") {
    if (ramBuf) { free(ramBuf); ramBuf=nullptr; ramSize=0; ramReady=false; }
    ramBuf = buf; ramSize = rx; ramReady = true;
  } else {
    free(buf);
  }
  return saved;
}

// ── Lấy size file từ JSON list ────────────────────────────────
int32_t getRemoteFileSizeNode2(const String& listJson, const String& fname) {
  int pos = 0;
  while (true) {
    int ni = listJson.indexOf("\"name\":\"", pos);
    if (ni < 0) break;
    ni += 8;
    int ne = listJson.indexOf("\"", ni);
    if (ne < 0) break;
    String n = listJson.substring(ni, ne);
    if (n == fname) {
      int si = listJson.indexOf("\"size\":", ne);
      if (si < 0) return -1;
      si += 7;
      while (si < (int)listJson.length() && listJson[si] == ' ') si++;
      String numStr = "";
      while (si < (int)listJson.length() && isDigit(listJson[si])) { numStr += listJson[si]; si++; }
      return numStr.toInt();
    }
    pos = ne + 1;
  }
  return -1;
}

// ── Kết nối trực tiếp vào Node-2 (không scan — tiết kiệm thời gian) ──────
bool findAndConnectNode2() {
  // Kết nối trực tiếp — biết SSID/pass rồi, không cần scan
  // Giữ WIFI_AP_STA để AP Node-1 vẫn sống trong khi kết nối STA
  Serial.printf("[Sync2] Kết nối vào '%s'...\n", NODE2_SSID);
  WiFi.begin(NODE2_SSID, NODE2_PASSWORD);
  
  // Chờ tối đa 8 giây, vừa handleClient() vừa đợi
  int retries = 0;
  int maxRetries = 16;  // 16 × 500ms = 8 giây
  while (WiFi.status() != WL_CONNECTED && retries < maxRetries) {
    unsigned long tw = millis();
    while (millis()-tw < 500) { server.handleClient(); delay(5); }
    Serial.print(".");
    retries++;
  }
  
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\n[Sync2] THẤT BẠI: Không tìm thấy Thiết bị B");
    WiFi.disconnect(false);
    return false;
  }
  return true;
}

// ── Đồng bộ từ Node-2 → Node-1 ───────────────────────────────
bool syncFromNode2() {
  Serial.println("\n[Sync2] ══ Bắt đầu kết nối Thiết bị B (Node-2) ══");

  // Tìm và kết nối Node-2 (với scan + timeout dài)
  if (!findAndConnectNode2()) {
    return false;
  }
  Serial.printf("\n[Sync2] Đã kết nối Thiết bị B  IP: %s\n",
                WiFi.localIP().toString().c_str());
  delay(300);

  // Lấy danh sách file từ Node-2
  String listJson = "";
  for (int attempt = 0; attempt < 3; attempt++) {
    listJson = httpGetFromNode2("/file/list", 4000);
    if (listJson.length() > 10 && listJson.indexOf("\"name\"") >= 0) break;
    Serial.printf("[Sync2] /file/list trống (lần %d/3) — thử lại...\n", attempt+1);
    unsigned long tw = millis();
    while (millis()-tw < 400) { server.handleClient(); delay(5); }
  }

  // Đếm file Node-2
  int node2Count = 0;
  {
    int p = 0;
    while (listJson.indexOf("\"name\":\"", p) >= 0) {
      int ni = listJson.indexOf("\"name\":\"", p) + 8;
      int ne = listJson.indexOf("\"", ni);
      if (ne < 0) break;
      node2Count++;
      p = ne + 1;
    }
  }
  Serial.printf("[Sync2] Danh sách: %d file (Thiết bị B)\n", node2Count);

  if (node2Count == 0) {
    Serial.println("[Sync2] Thiết bị B không có file — bỏ qua");
    WiFi.disconnect(false); delay(300);
    return false;
  }

  // Parse tên file từ Node-2
  std::vector<String> remoteFiles;
  int pos = 0;
  while (true) {
    int ni = listJson.indexOf("\"name\":\"", pos);
    if (ni < 0) break;
    ni += 8;
    int ne = listJson.indexOf("\"", ni);
    if (ne < 0) break;
    String fname = listJson.substring(ni, ne);
    if (fname.length() > 0) remoteFiles.push_back(fname);
    pos = ne + 1;
  }

  int downloaded = 0, skipped = 0, updated = 0;
  for (auto& fname : remoteFiles) {
    String path = "/" + fname;
    int32_t remoteSize = getRemoteFileSizeNode2(listJson, fname);

    if (SPIFFS.exists(path)) {
      File f = SPIFFS.open(path, "r");
      size_t localSize = f ? f.size() : 0;
      if (f) f.close();
      if (remoteSize > 0 && (int32_t)localSize == remoteSize) {
        Serial.printf("[Sync2] Bỏ qua '%s' — đã có (%d bytes)\n", fname.c_str(), localSize);
        skipped++; continue;
      }
      Serial.printf("[Sync2] Cập nhật '%s' — local=%d remote=%d bytes\n",
                    fname.c_str(), localSize, remoteSize);
      SPIFFS.remove(path);
      if (httpDownloadFileFromNode2(fname)) { downloaded++; updated++; }
    } else {
      Serial.printf("[Sync2] Tải mới '%s' (%d bytes)\n", fname.c_str(), remoteSize);
      if (httpDownloadFileFromNode2(fname)) downloaded++;
    }
    delay(80);
  }

  if (downloaded > 0) blinkLED(5, 100);
  WiFi.disconnect(false); delay(300);
  // AP Node-1 vẫn sống vì chưa bao giờ tắt (AP_STA mode)
  Serial.printf("[Sync2] Đã ngắt kết nối Thiết bị B. AP Node-1 vẫn chạy (AP_STA).\n");
  Serial.printf("[Sync2] Kết quả: tải %d, cập nhật %d, bỏ qua %d / %d file\n",
                downloaded, updated, skipped, (int)remoteFiles.size());
  return (downloaded > 0);
}

// ── Loop ──────────────────────────────────────────────────────
static unsigned long _lastSync2     = 0;
static bool          _syncing2      = false;

// _node1SyncBusy được set khi Node-1 bắt đầu act như STA
// handleSyncBusy() đọc flag này để Node-2 biết không nên kết nối

void loop() {
  checkBootButton();
  if (!nodeEnabled) { delay(10); return; }

  server.handleClient();

  WiFiClient c = audioServer.accept();
  if (c) {
    unsigned long t = millis();
    while (!c.available() && c.connected() && (millis()-t) < 3000) delay(1);
    if (c.available()) handleRawTCP(c);
    c.stop();
    _lastSync2 = millis();
  }

  // Raw TCP Upload Server port 8081 — bypass WebServer body issue
  WiFiClient uc = uploadServer.accept();
  if (uc) {
    unsigned long t = millis();
    while (!uc.available() && uc.connected() && (millis()-t) < 5000) delay(2);
    if (uc.available()) handleRawUpload(uc);
    uc.stop();
    _lastSync2 = millis();  // hoãn sync sau upload
  }

  // Periodic sync từ Node-2 mỗi SYNC_INTERVAL_MS:
  // - Không sync nếu đang có AP station kết nối (laptop đang dùng)
  // - Reset timer SAU KHI sync kết thúc để tránh overlap liên tục
  if (!_syncing2 && (millis() - _lastSync2 >= SYNC_INTERVAL_MS)) {
    // Kiểm tra có AP client không — nếu có, hoãn sync
    uint8_t apClients = WiFi.softAPgetStationNum();
    if (apClients > 0) {
      Serial.printf("[Sync2] Hoãn sync — có %d client đang kết nối AP\n", apClients);
      _lastSync2 = millis();  // đặt lại timer, thử sau SYNC_INTERVAL_MS
    } else {
      _syncing2        = true;
      _node1SyncBusy   = true;   // báo Node-2 biết Node-1 đang bận
      int before = countSpiffsFiles();
      Serial.printf("\n[Sync2] ── Bắt đầu đồng bộ (Thiết bị A có %d file) ──\n", before);
      syncFromNode2();
      int after  = countSpiffsFiles();
      Serial.printf("[Sync2] Danh sách: %d file (Thiết bị A)\n", after);
      _node1SyncBusy   = false;
      _syncing2        = false;
      _lastSync2       = millis();  // reset SAU khi sync xong → tránh overlap ngay lập tức
    }
  }
}
