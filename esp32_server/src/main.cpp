/*
 * ESP32 NODE-1 — AP + SPIFFS File Server
 * ══════════════════════════════════════════════════════════════
 * Vai trò : Phát WiFi AP "ESP32-Node-1", lưu file vào SPIFFS
 *           File tồn tại sau khi tắt điện
 *
 * FLOW DEMO:
 *   TRƯỚC DEMO (laptop + USB):
 *     Laptop kết nối "ESP32-Node-1" → GUI upload WAV → lưu SPIFFS
 *
 *   KHI DEMO (pin dự phòng, không cần laptop):
 *     Node-2 boot → kết nối Node-1 → fetch file → lưu vào Node-2
 *
 *   LẤY FILE RA:
 *     Laptop kết nối "ESP32-Node-1" → GUI download → file về folder
 *
 * Endpoints (port 80):
 *   GET  /status          ← trạng thái
 *   GET  /file/info       ← thông tin file trong SPIFFS
 *   GET  /file/download   ← download file WAV
 *   POST /file/upload     ← upload file WAV (lưu vào SPIFFS)
 *   POST /file/clear      ← xóa file khỏi SPIFFS
 *   GET  /ram/info        ← RAM buffer info (WAV header)
 *   GET  /ram/hex         ← hex dump RAM
 *
 * Port 8080: Raw TCP WAV upload/download (tương thích firmware cũ)
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <SPIFFS.h>
#include <esp_sleep.h>
#include <vector>
#include "test_wav.h"

// ── Cấu hình Node-2 (để sync 2 chiều) ────────────────────────
#define NODE2_SSID      "ESP32-Node-2"
#define NODE2_PASSWORD  "12345678"
#define NODE2_IP        "192.168.5.1"
#define NODE2_HTTP_PORT 80

// ── Cấu hình Node-1 ───────────────────────────────────────────
#define NODE_ID        1
#define AP_SSID        "ESP32-Node-1"
#define AP_PASSWORD    "12345678"
#define AP_CHANNEL     1
#define AP_HIDDEN      false          // Hiện SSID để dễ kết nối
#define AP_MAX_CON     4
#define AP_IP_1        192
#define AP_IP_2        168
#define AP_IP_3        4
#define AP_IP_4        1

#define LED_PIN        2
#define BOOT_PIN       0              // Nút BOOT tích hợp (GPIO 0, active LOW)
#define HTTP_PORT      80
#define AUDIO_PORT     8080
#define SPIFFS_FILE    "/audio.wav"
#define MAX_FILE_SIZE  1800000        // 1.8 MB tối đa SPIFFS (no_ota: 1.875 MB)

WebServer  server(HTTP_PORT);
WiFiServer audioServer(AUDIO_PORT);

// ── RAM buffer (tạm thời khi nhận/gửi) ───────────────────────
uint8_t* ramBuf  = nullptr;
size_t   ramSize = 0;
bool     ramReady = false;

// ── BOOT button toggle state ──────────────────────────────────
// nodeEnabled = true  → AP bật, server chạy bình thường
// nodeEnabled = false → AP tắt, server tạm dừng (tiết kiệm điện)
bool     nodeEnabled      = true;
bool     lastRawBoot      = HIGH;   // lần đọc raw trước (chống rung)
bool     stableBoot       = HIGH;   // trạng thái đã ổn định sau debounce
unsigned long lastDebounceMs = 0;
#define  DEBOUNCE_MS      50        // 50ms chống rung

// ── LED ───────────────────────────────────────────────────────
void blinkLED(int times, int ms = 100) {
  for (int i = 0; i < times; i++) {
    digitalWrite(LED_PIN, LOW);  delay(ms);
    digitalWrite(LED_PIN, HIGH); delay(ms);
  }
}

// ── Bật / Tắt node ────────────────────────────────────────────
void enableNode() {
  nodeEnabled = true;
  // Khởi động lại AP
  WiFi.mode(WIFI_AP_STA);
  IPAddress apIP(AP_IP_1,AP_IP_2,AP_IP_3,AP_IP_4);
  IPAddress gw(AP_IP_1,AP_IP_2,AP_IP_3,AP_IP_4);
  IPAddress sn(255,255,255,0);
  WiFi.softAPConfig(apIP,gw,sn);
  WiFi.softAP(AP_SSID,AP_PASSWORD,AP_CHANNEL,AP_HIDDEN,AP_MAX_CON);
  server.begin();
  audioServer.begin();
  digitalWrite(LED_PIN, HIGH);
  Serial.println("[BOOT] Node-1 ENABLED — AP ON");
  blinkLED(3, 80);
}

void disableNode() {
  nodeEnabled = false;
  WiFi.softAPdisconnect(true);   // Tắt AP, ngắt tất cả client
  WiFi.disconnect(true);         // Tắt STA
  WiFi.mode(WIFI_OFF);
  digitalWrite(LED_PIN, LOW);
  Serial.println("[BOOT] Node-1 DISABLED — AP OFF (low power)");
  blinkLED(2, 300);
  digitalWrite(LED_PIN, LOW);   // Đảm bảo LED tắt hẳn
}

// ── Đọc nút BOOT với debounce chuẩn ────────────────────────
void checkBootButton() {
  bool raw = digitalRead(BOOT_PIN);
  // Nếu tín hiệu thay đổi → reset timer chống rung
  if (raw != lastRawBoot) {
    lastDebounceMs = millis();
    lastRawBoot = raw;
  }
  // Sau khi ổn định đủ DEBOUNCE_MS → cập nhật trạng thái ổn định
  if ((millis() - lastDebounceMs) >= DEBOUNCE_MS) {
    if (raw != stableBoot) {
      // Phát hiện cạnh xuống (HIGH→LOW) = nhấn nút
      if (stableBoot == HIGH && raw == LOW) {
        if (nodeEnabled) disableNode();
        else             enableNode();
      }
      stableBoot = raw;
    }
  }
}

// ── SPIFFS helpers ────────────────────────────────────────────
bool spiffsHasFile() {
  return SPIFFS.exists(SPIFFS_FILE);
}

size_t spiffsFileSize() {
  if (!spiffsHasFile()) return 0;
  File f = SPIFFS.open(SPIFFS_FILE, "r");
  if (!f) return 0;
  size_t sz = f.size();
  f.close();
  return sz;
}

bool spiffsSave(const uint8_t* buf, size_t size) {
  File f = SPIFFS.open(SPIFFS_FILE, "w");
  if (!f) {
    Serial.println("[SPIFFS] Open for write FAILED");
    return false;
  }
  size_t written = f.write(buf, size);
  f.close();
  bool ok = (written == size);
  Serial.printf("[SPIFFS] Save %d/%d bytes → %s\n", written, size, ok?"OK":"FAIL");
  return ok;
}

// Lưu vào SPIFFS với path tùy ý (tự động format + retry nếu SPIFFS fragmented)
bool spiffsSaveAs(const uint8_t* buf, size_t size, const String& path) {
  if (size > SPIFFS.totalBytes()) {
    Serial.printf("[SPIFFS] File too large: need %d, total %d bytes\n", size, SPIFFS.totalBytes());
    return false;
  }
  // Thử ghi lần 1
  {
    File f = SPIFFS.open(path, "w");
    if (f) {
      size_t wr = f.write(buf, size); f.close();
      if (wr == size) {
        Serial.printf("[SPIFFS] SaveAs '%s' %d/%d → OK\n", path.c_str(), wr, size);
        return true;
      }
      SPIFFS.remove(path);
      Serial.printf("[SPIFFS] SaveAs '%s' FAILED (%d/%d) — fragmented, auto-format...\n", path.c_str(), wr, size);
    } else {
      Serial.printf("[SPIFFS] Open '%s' FAILED — try format...\n", path.c_str());
    }
  }
  // Format để xóa fragmentation, rồi retry
  Serial.println("[SPIFFS] Formatting...");
  SPIFFS.format();
  if (!SPIFFS.begin(true)) {
    Serial.println("[SPIFFS] Re-mount FAILED"); return false;
  }
  Serial.printf("[SPIFFS] After format: free=%d bytes\n", SPIFFS.totalBytes() - SPIFFS.usedBytes());
  // Ghi lần 2
  {
    File f2 = SPIFFS.open(path, "w");
    if (!f2) { Serial.printf("[SPIFFS] Open '%s' after format FAILED\n", path.c_str()); return false; }
    size_t wr2 = f2.write(buf, size); f2.close();
    bool ok2 = (wr2 == size);
    if (!ok2) SPIFFS.remove(path);
    Serial.printf("[SPIFFS] SaveAs '%s' retry: %d/%d → %s\n", path.c_str(), wr2, size, ok2?"OK":"FAIL");
    return ok2;
  }
}

bool spiffsLoad() {
  if (!spiffsHasFile()) return false;
  File f = SPIFFS.open(SPIFFS_FILE, "r");
  if (!f) return false;
  size_t sz = f.size();
  if (sz == 0 || sz > MAX_FILE_SIZE) { f.close(); return false; }
  if (ramBuf) { free(ramBuf); ramBuf = nullptr; ramSize = 0; }
  ramBuf = (uint8_t*)malloc(sz);
  if (!ramBuf) { f.close(); Serial.println("[SPIFFS] OOM load"); return false; }
  size_t rd = f.read(ramBuf, sz);
  f.close();
  ramSize  = rd;
  ramReady = (rd >= 44);
  Serial.printf("[SPIFFS] Load %d bytes → %s\n", rd, ramReady?"OK":"FAIL");
  return ramReady;
}

// ── Filename helpers ──────────────────────────────────────────
// Sanitize tên file: chỉ giữ ký tự an toàn, thêm .wav nếu thiếu
String sanitizeFilename(String name) {
  name.trim();
  String out = "";
  for (int i = 0; i < (int)name.length() && i < 32; i++) {
    char c = name[i];
    if (isAlphaNumeric(c) || c=='.' || c=='-' || c=='_') out += c;
  }
  if (out.length() == 0) return "";
  if (!out.endsWith(".wav") && !out.endsWith(".WAV")) out += ".wav";
  return out;
}

// Generate tên tự động audio_0001.wav, audio_0002.wav, ...
static uint16_t _fileCounter = 0;
String genAutoFilename() {
  _fileCounter++;
  char buf[24];
  snprintf(buf, sizeof(buf), "audio_%04d.wav", _fileCounter);
  return String(buf);
}

// ── Format helpers ────────────────────────────────────────────
String formatUptime(uint32_t ms) {
  uint32_t s = ms/1000, m = s/60; s%=60;
  uint32_t h = m/60;              m%=60;
  char b[32]; snprintf(b,sizeof(b),"%02d:%02d:%02d",h,m,s);
  return String(b);
}

String wavInfoJson(const uint8_t* buf, size_t size) {
  if (!buf || size < 44) return "{}";
  if (buf[0]!='R'||buf[1]!='I'||buf[2]!='F'||buf[3]!='F') return "{\"is_wav\":false}";
  if (buf[8]!='W'||buf[9]!='A'||buf[10]!='V'||buf[11]!='E') return "{\"is_wav\":false}";
  uint16_t fmt   = buf[20]|(buf[21]<<8);
  uint16_t ch    = buf[22]|(buf[23]<<8);
  uint32_t sr    = buf[24]|(buf[25]<<8)|(buf[26]<<16)|(buf[27]<<24);
  uint32_t br    = buf[28]|(buf[29]<<8)|(buf[30]<<16)|(buf[31]<<24);
  uint16_t ba    = buf[32]|(buf[33]<<8);
  uint16_t bps   = buf[34]|(buf[35]<<8);
  uint32_t dsz   = buf[40]|(buf[41]<<8)|(buf[42]<<16)|(buf[43]<<24);
  float    dur   = (sr>0&&ch>0&&bps>0)?(float)dsz/(sr*ch*(bps/8)):0.0f;
  String j = "{\"is_wav\":true";
  j += ",\"format\":\"" + String(fmt==1?"PCM":fmt==3?"FLOAT":"OTHER") + "\"";
  j += ",\"channels\":"     + String(ch);
  j += ",\"sample_rate\":"  + String(sr);
  j += ",\"byte_rate\":"    + String(br);
  j += ",\"block_align\":"  + String(ba);
  j += ",\"bits_per_sample\":" + String(bps);
  j += ",\"data_size\":"    + String(dsz);
  j += ",\"duration_sec\":" + String(dur,2);
  j += "}";
  return j;
}

// ── HTTP Handlers ─────────────────────────────────────────────

// GET /status
void handleStatus() {
  bool hasSpiffs = spiffsHasFile();
  size_t spiffsSz = spiffsFileSize();
  server.send(200, "application/json",
    String("{\"node\":1") +
    ",\"ap_ssid\":\"" + AP_SSID + "\"" +
    ",\"ip\":\"192.168.4.1\"" +
    ",\"uptime\":\"" + formatUptime(millis()) + "\"" +
    ",\"free_heap\":" + String(ESP.getFreeHeap()) +
    ",\"spiffs_has_file\":" + (hasSpiffs?"true":"false") +
    ",\"spiffs_size\":" + String(spiffsSz) +
    ",\"ram_ready\":" + (ramReady?"true":"false") +
    ",\"ram_size\":" + String(ramSize) +
    ",\"builtin_wav_size\":" + String(TEST_WAV_SIZE) + "}");
}

// GET /file/info — thông tin file trong SPIFFS
void handleFileInfo() {
  bool has = spiffsHasFile();
  size_t sz = spiffsFileSize();
  String j = "{\"has_file\":" + String(has?"true":"false");
  j += ",\"path\":\"" + String(SPIFFS_FILE) + "\"";
  j += ",\"size\":" + String(sz);
  j += ",\"size_kb\":" + String(sz/1024.0f,1);
  if (has && ramReady && ramBuf) {
    j += ",\"wav_info\":" + wavInfoJson(ramBuf, ramSize);
  } else if (has) {
    // Đọc 44 bytes đầu để check WAV header
    File f = SPIFFS.open(SPIFFS_FILE,"r");
    if (f && f.size() >= 44) {
      uint8_t hdr[44]; f.read(hdr,44); f.close();
      j += ",\"wav_info\":" + wavInfoJson(hdr, 44);
    }
  }
  j += ",\"free_heap\":" + String(ESP.getFreeHeap()) + "}";
  server.send(200, "application/json", j);
}

// GET /file/download[?name=<filename>] — download file WAV từ SPIFFS
void handleFileDownload() {
  // Nếu có ?name= → download file theo tên (dùng cho Node-2 sync)
  String reqName = server.arg("name");
  reqName.trim();
  if (reqName.length() > 0) {
    String safe = sanitizeFilename(reqName);
    String path = safe.startsWith("/") ? safe : ("/" + safe);
    File f = SPIFFS.open(path, "r");
    if (f) {
      size_t sz = f.size();
      server.sendHeader("Content-Disposition", "attachment; filename=" + safe);
      server.sendHeader("Content-Length", String(sz));
      server.setContentLength(sz);
      server.send(200, "audio/wav", "");
      uint8_t buf[1024]; size_t sent = 0;
      while (sent < sz) {
        size_t rd = f.read(buf, min((size_t)1024, sz-sent));
        server.sendContent((char*)buf, rd); sent += rd;
      }
      f.close();
      Serial.printf("[Download] Sent '%s' %d bytes\n", path.c_str(), sz);
      blinkLED(3,100);
      return;
    }
    server.send(404,"application/json","{\"error\":\"file not found\"}");
    return;
  }
  // Không có ?name= → serve SPIFFS_FILE → RAM → builtin (backward compat)
  if (spiffsHasFile()) {
    File f = SPIFFS.open(SPIFFS_FILE, "r");
    if (f) {
      size_t sz = f.size();
      server.sendHeader("Content-Disposition", "attachment; filename=audio.wav");
      server.sendHeader("Content-Length", String(sz));
      server.setContentLength(sz);
      server.send(200, "audio/wav", "");
      uint8_t buf[1024];
      size_t sent = 0;
      while (sent < sz) {
        size_t rd = f.read(buf, min((size_t)1024, sz-sent));
        server.sendContent((char*)buf, rd);
        sent += rd;
      }
      f.close();
      Serial.printf("[Download] Sent %d bytes from SPIFFS\n", sz);
      blinkLED(3,100);
      return;
    }
  }
  if (ramReady && ramSize > 0) {
    server.sendHeader("Content-Disposition","attachment; filename=audio.wav");
    server.send_P(200,"audio/wav",(const char*)ramBuf,ramSize);
    blinkLED(3,100);
    return;
  }
  if (TEST_WAV_SIZE > 0) {
    server.sendHeader("Content-Disposition","attachment; filename=audio.wav");
    server.send_P(200,"audio/wav",(const char*)TEST_WAV_DATA,TEST_WAV_SIZE);
    blinkLED(3,100);
    return;
  }
  server.send(404,"application/json","{\"error\":\"no file\"}");
}

// POST /file/upload — nhận file WAV, lưu vào SPIFFS
void handleFileUpload() {
  if (!server.hasArg("plain") || server.arg("plain").length() == 0) {
    // Không có body dạng plain text → đây là upload nhị phân qua HTTP
    server.send(400,"application/json","{\"error\":\"use raw TCP port 8080 or multipart\"}");
    return;
  }
  server.send(200,"application/json","{\"status\":\"use raw TCP port 8080\"}");
}

// POST /file/clear — xóa file SPIFFS
void handleFileClear() {
  bool ok = SPIFFS.remove(SPIFFS_FILE);
  if (ramBuf) { free(ramBuf); ramBuf=nullptr; ramSize=0; ramReady=false; }
  server.send(200,"application/json",
    ok ? "{\"status\":\"ok\",\"message\":\"File da xoa\"}"
       : "{\"status\":\"ok\",\"message\":\"Khong co file de xoa\"}");
  Serial.println("[SPIFFS] File cleared");
}

// GET /ram/info
void handleRamInfo() {
  if (!ramReady || ramSize < 44) {
    server.send(200,"application/json",
      String("{\"ram_ready\":false,\"free_heap\":") + String(ESP.getFreeHeap()) +
      ",\"spiffs_has_file\":" + (spiffsHasFile()?"true":"false") + "}");
    return;
  }
  const uint8_t* buf = ramBuf;
  char magic[5]={0}; memcpy(magic,buf,4);
  String j = "{\"ram_ready\":true";
  j += ",\"size_bytes\":" + String(ramSize);
  j += ",\"magic\":\"" + String(magic) + "\"";
  j += ",\"wav_info\":" + wavInfoJson(buf,ramSize);
  j += ",\"free_heap\":" + String(ESP.getFreeHeap()) + "}";
  server.send(200,"application/json",j);
}

// GET /ram/hex?offset=0&len=64
void handleRamHex() {
  int off = server.hasArg("offset")?server.arg("offset").toInt():0;
  int len = server.hasArg("len")   ?server.arg("len").toInt()   :64;
  if (len>256) len=256;
  const uint8_t* buf = nullptr; size_t sz=0; String src="none";
  if (ramReady&&ramSize>0){buf=ramBuf;sz=ramSize;src="ram";}
  else if(TEST_WAV_SIZE>0){buf=TEST_WAV_DATA;sz=TEST_WAV_SIZE;src="builtin";}
  if(!buf){server.send(200,"application/json","{\"error\":\"no data\"}");return;}
  if((size_t)off>=sz){server.send(200,"application/json","{\"error\":\"offset OOB\"}");return;}
  int avail=sz-off; if(len>avail)len=avail;
  String j="{\"source\":\""+src+"\",\"total\":"+String(sz)+",\"offset\":"+String(off)+",\"len\":"+String(len);
  j+=",\"hex\":\"";
  for(int i=0;i<len;i++){char t[4];snprintf(t,sizeof(t),"%02X",buf[off+i]);if(i)j+=" ";j+=t;}
  j+="\",\"ascii\":\"";
  for(int i=0;i<len;i++){uint8_t c=buf[off+i];j+=(c>=32&&c<127)?(char)c:'.';}
  j+="\"}";
  server.send(200,"application/json",j);
}

// GET /file/list — danh sách file trong SPIFFS
void handleFileList() {
  // Bước 1: thu thập tên file trước (đóng iterator)
  std::vector<String> names;
  {
    File root = SPIFFS.open("/");
    File fi = root.openNextFile();
    while (fi) {
      if (!fi.isDirectory()) names.push_back(String(fi.name()));
      fi.close();
      fi = root.openNextFile();
    }
    root.close();
  }
  // Bước 2: mở lại từng file để lấy size + WAV header chính xác
  String j = "{\"files\":[";
  int count = 0;
  for (auto& fname : names) {
    // Đảm bảo path có leading slash
    String path = fname.startsWith("/") ? fname : ("/" + fname);
    // Tên hiển thị không có leading slash
    String displayName = fname.startsWith("/") ? fname.substring(1) : fname;
    File f2 = SPIFFS.open(path, "r");
    size_t sz = f2 ? f2.size() : 0;
    // Fallback: nếu size=0 nhưng file này đang load trong RAM thì dùng ramSize
    if (sz == 0 && ramReady && ramSize > 0 && path == String(SPIFFS_FILE)) {
      sz = ramSize;
    }
    // Bỏ qua file rỗng/corrupt (size=0 sau fallback)
    if (sz == 0) {
      if (f2) f2.close();
      Serial.printf("[FileList] Skip empty '%s'\n", displayName.c_str());
      continue;
    }
    float dur = 0.0f;
    if (sz >= 44) {
      // Ưu tiên dùng RAM buffer nếu là file đang load
      if (ramReady && ramBuf && ramSize >= 44 && path == String(SPIFFS_FILE)) {
        uint16_t ch  = ramBuf[22]|(ramBuf[23]<<8);
        uint32_t sr  = ramBuf[24]|(ramBuf[25]<<8)|(ramBuf[26]<<16)|(ramBuf[27]<<24);
        uint16_t bps = ramBuf[34]|(ramBuf[35]<<8);
        uint32_t dsz = ramBuf[40]|(ramBuf[41]<<8)|(ramBuf[42]<<16)|(ramBuf[43]<<24);
        if (sr > 0 && ch > 0 && bps > 0) dur = (float)dsz / (sr * ch * (bps/8));
      } else if (f2) {
        uint8_t hdr[44];
        f2.seek(0);
        f2.read(hdr, 44);
        uint16_t ch  = hdr[22]|(hdr[23]<<8);
        uint32_t sr  = hdr[24]|(hdr[25]<<8)|(hdr[26]<<16)|(hdr[27]<<24);
        uint16_t bps = hdr[34]|(hdr[35]<<8);
        uint32_t dsz = hdr[40]|(hdr[41]<<8)|(hdr[42]<<16)|(hdr[43]<<24);
        if (sr > 0 && ch > 0 && bps > 0) dur = (float)dsz / (sr * ch * (bps/8));
      }
    }
    if (f2) f2.close();
    if (count > 0) j += ",";
    char sz_kb[16]; snprintf(sz_kb, sizeof(sz_kb), "%.1f KB", sz/1024.0f);
    j += "{\"name\":\"" + displayName + "\"";
    j += ",\"path\":\"" + path + "\"";
    j += ",\"size\":" + String(sz);
    j += ",\"size_kb\":\"" + String(sz_kb) + "\"";
    j += ",\"duration_sec\":" + String(dur, 2) + "}";
    count++;
  }
  j += "],\"count\":" + String(count);
  j += ",\"spiffs_total\":" + String(SPIFFS.totalBytes());
  j += ",\"spiffs_used\":"  + String(SPIFFS.usedBytes());
  j += ",\"spiffs_free\":"  + String(SPIFFS.totalBytes() - SPIFFS.usedBytes()) + "}";
  server.send(200, "application/json", j);
}

// POST /shutdown — disabled: Node-1 luôn chạy để phục vụ laptop + sync
void handleShutdown() {
  Serial.println("[Shutdown] Request ignored — Node-1 stays awake");
  server.send(200, "application/json", "{\"status\":\"ok\",\"message\":\"Node-1 shutdown disabled\"}");
}

void handleNotFound() {
  server.send(404,"application/json","{\"error\":\"not found\"}");
}

// ── Raw TCP port 8080 (upload/download WAV) ───────────────────
void handleRawTCP(WiFiClient& client) {
  String remote = client.remoteIP().toString();
  String req = client.readStringUntil('\n'); req.trim();
  Serial.printf("[TCP] %s from %s\n", req.c_str(), remote.c_str());

  int clen = 0;
  String xFilename = "";
  while (client.connected()) {
    String line = client.readStringUntil('\n'); line.trim();
    if (line.length()==0) break;
    String lo=line; lo.toLowerCase();
    if (lo.startsWith("content-length:"))
      clen = line.substring(line.indexOf(':')+1).toInt();
    if (lo.startsWith("x-filename:")) {
      xFilename = line.substring(line.indexOf(':')+1);
      xFilename.trim();
    }
  }

  if (req.startsWith("POST")) {
    if (clen<=0 || clen>(int)MAX_FILE_SIZE) {
      client.print("HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n{\"error\":\"bad length\"}");
      return;
    }
    if (ramBuf){free(ramBuf);ramBuf=nullptr;ramSize=0;}
    ramBuf = (uint8_t*)malloc(clen);
    if (!ramBuf){
      client.print("HTTP/1.1 507 Insufficient Storage\r\nConnection: close\r\n\r\n{\"error\":\"oom\"}");
      return;
    }
    size_t rx=0; unsigned long t=millis();
    while(rx<(size_t)clen&&client.connected()&&(millis()-t)<20000){
      size_t av=client.available();
      if(av>0){size_t ch=min(av,(size_t)(clen-rx));client.readBytes(ramBuf+rx,ch);rx+=ch;t=millis();}
      else delay(1);
    }
    ramSize=rx; ramReady=(rx>=44);
    Serial.printf("[TCP] Rx %d/%d bytes\n",rx,clen);
    // Lưu vào SPIFFS
    if (ramReady) {
      String saveAs = sanitizeFilename(xFilename);
      if (saveAs.length() == 0) saveAs = genAutoFilename();
      bool saved = spiffsSaveAs(ramBuf, ramSize, "/" + saveAs);
      // Backward compat: nếu tên đúng là audio.wav thì cũng cập nhật SPIFFS_FILE
      if (saved && saveAs == String("audio.wav")) {
        // already saved to /audio.wav — no extra action needed
      }
      String resp = "{\"status\":\"ok\",\"received\":" + String(rx) +
                    ",\"filename\":\"" + saveAs + "\"" +
                    ",\"spiffs_saved\":" + String(saved?"true":"false") + "}";
      client.printf("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s",
                    resp.length(), resp.c_str());
      blinkLED(5,80);
    } else {
      client.print("HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n{\"error\":\"incomplete\"}");
    }

  } else if (req.startsWith("GET")) {
    // Ưu tiên SPIFFS → RAM → builtin
    if (spiffsHasFile()) {
      File f = SPIFFS.open(SPIFFS_FILE,"r");
      if (f) {
        size_t sz=f.size();
        client.printf("HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\nContent-Length: %d\r\n"
                      "Content-Disposition: attachment; filename=audio.wav\r\nConnection: close\r\n\r\n",sz);
        uint8_t buf[1024]; size_t sent=0;
        while(sent<sz&&client.connected()){
          size_t rd=f.read(buf,min((size_t)1024,sz-sent));
          client.write(buf,rd); sent+=rd;
        }
        f.close(); client.flush();
        Serial.printf("[TCP] Sent %d bytes from SPIFFS\n",sz);
        blinkLED(3,100); return;
      }
    }
    const uint8_t* buf=(ramReady&&ramSize>0)?ramBuf:TEST_WAV_DATA;
    size_t sz=(ramReady&&ramSize>0)?ramSize:TEST_WAV_SIZE;
    if(sz==0){client.print("HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n");return;}
    client.printf("HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\nContent-Length: %d\r\n"
                  "Content-Disposition: attachment; filename=audio.wav\r\nConnection: close\r\n\r\n",sz);
    size_t sent=0;
    while(sent<sz&&client.connected()){
      size_t ch=min((size_t)1024,sz-sent);
      client.write(buf+sent,ch); sent+=ch;
    }
    client.flush();
    Serial.printf("[TCP] Sent %d bytes from %s\n",sz,(ramReady&&ramSize>0)?"RAM":"builtin");
    blinkLED(3,100);
  }
}

// POST /file/delete?name=<filename> — xóa file SPIFFS theo tên
void handleFileDelete() {
  String name = server.hasArg("name") ? server.arg("name") : "";
  if (name.length() == 0) {
    server.send(400, "application/json", "{\"error\":\"missing name param\"}");
    return;
  }
  String safe = sanitizeFilename(name);
  if (safe.length() == 0) {
    server.send(400, "application/json", "{\"error\":\"invalid filename\"}");
    return;
  }
  String path = "/" + safe;
  if (!SPIFFS.exists(path)) {
    server.send(404, "application/json",
      "{\"error\":\"file not found\",\"path\":\"" + path + "\"}");
    return;
  }
  bool ok = SPIFFS.remove(path);
  // Nếu xóa file đang load trong RAM → clear RAM
  if (ok && ramReady && path == String(SPIFFS_FILE)) {
    if (ramBuf) { free(ramBuf); ramBuf=nullptr; ramSize=0; ramReady=false; }
  }
  server.send(200, "application/json",
    ok ? "{\"status\":\"ok\",\"deleted\":\"" + path + "\"}"
       : "{\"error\":\"delete failed\"}");
  Serial.printf("[SPIFFS] Delete '%s' → %s\n", path.c_str(), ok?"OK":"FAIL");
}

// ── Sync từ Node-2 (APSTA mode, chạy lúc boot) ───────────────
String httpGetFromNode2(const char* path, int timeoutMs = 5000) {
  WiFiClient c;
  if (!c.connect(NODE2_IP, NODE2_HTTP_PORT)) return "";
  c.printf("GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n", path, NODE2_IP);
  String body = "";
  bool inBody = false;
  unsigned long t = millis();
  while (c.connected() && (millis()-t) < (unsigned long)timeoutMs) {
    if (c.available()) {
      String line = c.readStringUntil('\n'); line.trim();
      if (inBody) { body += line; }
      else if (line.length() == 0) { inBody = true; }
      t = millis();
    } else delay(1);
  }
  c.stop();
  return body;
}

bool httpDownloadFileFromNode2(const String& filename) {
  String path = "/file/download?name=" + filename;
  WiFiClient c;
  if (!c.connect(NODE2_IP, NODE2_HTTP_PORT)) {
    Serial.printf("[SyncN2] HTTP connect FAILED for '%s'\n", filename.c_str());
    return false;
  }
  c.printf("GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n",
           path.c_str(), NODE2_IP);
  int contentLength = 0;
  unsigned long t = millis();
  while (c.connected() && (millis()-t) < 5000) {
    String line = c.readStringUntil('\n'); line.trim();
    if (line.length() == 0) break;
    String lo = line; lo.toLowerCase();
    if (lo.startsWith("content-length:"))
      contentLength = line.substring(line.indexOf(':')+1).toInt();
    t = millis();
  }
  if (contentLength <= 0 || contentLength > (int)MAX_FILE_SIZE) {
    c.stop();
    Serial.printf("[SyncN2] Bad CL=%d for '%s'\n", contentLength, filename.c_str());
    return false;
  }
  uint8_t* buf = (uint8_t*)malloc(contentLength);
  if (!buf) { c.stop(); Serial.println("[SyncN2] OOM"); return false; }
  size_t rx = 0; t = millis();
  while (rx < (size_t)contentLength && c.connected() && (millis()-t) < 20000) {
    size_t av = c.available();
    if (av > 0) {
      size_t ch = min(av, (size_t)(contentLength-rx));
      c.readBytes(buf+rx, ch); rx += ch; t = millis();
    } else delay(1);
  }
  c.stop();
  if (rx < 44) { free(buf); Serial.printf("[SyncN2] Incomplete '%s'\n", filename.c_str()); return false; }
  bool saved = spiffsSaveAs(buf, rx, "/" + filename);
  if (saved && filename == "audio.wav") {
    if (ramBuf) { free(ramBuf); ramBuf = nullptr; ramSize = 0; ramReady = false; }
    ramBuf = buf; ramSize = rx; ramReady = true;
  } else {
    free(buf);
  }
  Serial.printf("[SyncN2] '%s' %d bytes → %s\n", filename.c_str(), rx, saved?"OK":"FAIL");
  return saved;
}

void syncFromNode2() {
  Serial.println("\n[SyncN2] Connecting to Node-2...");
  // Không đổi WiFi.mode() — AP_STA đã set từ setup(), AP vẫn chạy song song
  WiFi.begin(NODE2_SSID, NODE2_PASSWORD);
  int retries = 0;
  while (WiFi.status() != WL_CONNECTED && retries < 12) {
    // Serve HTTP clients trong lúc chờ STA kết nối để AP không bị drop
    unsigned long tw = millis();
    while (millis() - tw < 300) {
      server.handleClient();
      delay(5);
    }
    Serial.print("."); retries++;
  }
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\n[SyncN2] Node-2 not found — skip");
    WiFi.disconnect(false);
    return;
  }
  Serial.printf("\n[SyncN2] Connected. STA IP: %s\n", WiFi.localIP().toString().c_str());
  delay(200);

  // Lấy danh sách file từ Node-2
  String listJson = httpGetFromNode2("/file/list", 6000);
  Serial.printf("[SyncN2] /file/list: %s\n", listJson.substring(0,200).c_str());

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
  Serial.printf("[SyncN2] Node-2 has %d file(s)\n", remoteFiles.size());

  int downloaded = 0;
  for (auto& fname : remoteFiles) {
    String path = "/" + fname;
    if (SPIFFS.exists(path)) {
      Serial.printf("[SyncN2] Skip '%s' — already exists\n", fname.c_str());
      continue;
    }
    bool ok = httpDownloadFileFromNode2(fname);
    if (ok) downloaded++;
    delay(100);
  }

  // Nháy LED 3 cái khi có file mới được đồng bộ
  if (downloaded > 0) blinkLED(3, 100);

  // Chính sách đồng bộ: chỉ bổ sung file còn thiếu — KHÔNG xóa file local
  // (xóa file phải thực hiện thủ công trên từng node riêng biệt)

  WiFi.disconnect(false); delay(200);
  // Không gọi WiFi.mode() — giữ nguyên WIFI_AP_STA để AP không restart
  Serial.printf("[SyncN2] Done: +%d/%d new file(s) downloaded from Node-2\n", downloaded, (int)remoteFiles.size());
}

// ── Setup ─────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200); delay(500);
  pinMode(LED_PIN,OUTPUT); digitalWrite(LED_PIN,LOW);
  pinMode(BOOT_PIN, INPUT_PULLUP);  // Nút BOOT GPIO 0, active LOW

  Serial.println("\n══════════════════════════════");
  Serial.println(" ESP32 NODE-1  (AP + SPIFFS)");
  Serial.println("══════════════════════════════");

  // Init SPIFFS
  if (!SPIFFS.begin(true)) {
    Serial.println("[SPIFFS] Mount FAILED — format...");
    SPIFFS.format();
    SPIFFS.begin(true);
  }
  Serial.printf("[SPIFFS] Total:%d  Used:%d  Free:%d bytes\n",
    SPIFFS.totalBytes(), SPIFFS.usedBytes(),
    SPIFFS.totalBytes()-SPIFFS.usedBytes());

  // Load file từ SPIFFS vào RAM (nếu có)
  if (spiffsHasFile()) {
    Serial.printf("[SPIFFS] Found file: %d bytes — loading...\n", spiffsFileSize());
    spiffsLoad();
    if (ramReady) {
      Serial.printf("[SPIFFS] Loaded into RAM: %d bytes\n", ramSize);
      blinkLED(3,150); // 3 nhấp = có file sẵn
    }
  } else {
    Serial.println("[SPIFFS] No file — using built-in WAV");
    Serial.printf("[Info] Built-in WAV: %d bytes (%.1f KB)\n",
                  TEST_WAV_SIZE, TEST_WAV_SIZE/1024.0f);
    blinkLED(1,500); // 1 nhấp dài = chưa có file
  }

  // WiFi AP (dùng WIFI_AP_STA để có thể sync Node-2 sau)
  WiFi.mode(WIFI_AP_STA);
  IPAddress apIP(AP_IP_1,AP_IP_2,AP_IP_3,AP_IP_4);
  IPAddress gw(AP_IP_1,AP_IP_2,AP_IP_3,AP_IP_4);
  IPAddress sn(255,255,255,0);
  WiFi.softAPConfig(apIP,gw,sn);
  WiFi.softAP(AP_SSID,AP_PASSWORD,AP_CHANNEL,AP_HIDDEN,AP_MAX_CON);
  delay(200);
  Serial.printf("[AP] SSID    : %s\n", AP_SSID);
  Serial.printf("[AP] Password: %s\n", AP_PASSWORD);
  Serial.printf("[AP] IP      : %s\n", WiFi.softAPIP().toString().c_str());
  Serial.printf("[AP] Heap    : %d bytes\n", ESP.getFreeHeap());
  digitalWrite(LED_PIN,HIGH);

  // Sync file từ Node-2 (nếu Node-2 đang chạy gần đó)
  syncFromNode2();

  // HTTP API
  server.on("/status",        HTTP_GET,  handleStatus);
  server.on("/file/info",     HTTP_GET,  handleFileInfo);
  server.on("/file/download", HTTP_GET,  handleFileDownload);
  server.on("/file/upload",   HTTP_POST, handleFileUpload);
  server.on("/file/clear",    HTTP_POST, handleFileClear);
  server.on("/ram/info",      HTTP_GET,  handleRamInfo);
  server.on("/ram/hex",       HTTP_GET,  handleRamHex);
  // Tương thích firmware cũ
  server.on("/audio/info",    HTTP_GET,  handleFileInfo);
  server.on("/ram/clear",     HTTP_POST, handleFileClear);
  server.on("/file/list",   HTTP_GET,  handleFileList);
  server.on("/file/delete", HTTP_POST, handleFileDelete);
  server.on("/shutdown",    HTTP_POST, handleShutdown);
  server.onNotFound(handleNotFound);
  server.begin();

  audioServer.begin();

  Serial.println("\n[Ready] Node-1 Endpoints:");
  Serial.printf("  GET  http://192.168.4.1/status\n");
  Serial.printf("  GET  http://192.168.4.1/file/info      <- thong tin file SPIFFS\n");
  Serial.printf("  GET  http://192.168.4.1/file/download  <- download file\n");
  Serial.printf("  POST http://192.168.4.1/file/clear     <- xoa file\n");
  Serial.printf("  GET  http://192.168.4.1/ram/info       <- WAV header\n");
  Serial.printf("  POST http://192.168.4.1:8080/upload    <- upload WAV (TCP)\n");
  Serial.printf("  GET  http://192.168.4.1:8080/audio.wav <- download WAV (TCP)\n");
}

// ── Loop ──────────────────────────────────────────────────────
static unsigned long _lastSync2 = 0;
static bool _syncing = false;
#define SYNC_INTERVAL_MS 13000UL  // 13 giây (lệch với Node-2: 17s)

void loop() {
  // Luôn kiểm tra nút BOOT để toggle bật/tắt
  checkBootButton();

  // Nếu node đang bị tắt → không xử lý server / sync
  if (!nodeEnabled) {
    delay(10);
    return;
  }

  server.handleClient();
  WiFiClient c = audioServer.accept();
  if (c) {
    unsigned long t=millis();
    while(!c.available()&&c.connected()&&(millis()-t)<3000) delay(1);
    if (c.available()) handleRawTCP(c);
    c.stop();
    // Có client vừa kết nối → reset timer để không sync ngay sau đó
    _lastSync2 = millis();
  }
  // Periodic sync từ Node-2 mỗi 10s
  if (!_syncing && (millis() - _lastSync2 >= SYNC_INTERVAL_MS)) {
    _syncing = true;
    _lastSync2 = millis();
    syncFromNode2();
    _syncing = false;
  }
}
