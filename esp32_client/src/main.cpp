/*
 * ESP32 NODE-2 — APSTA + SPIFFS File Relay
 * ══════════════════════════════════════════════════════════════
 * Vai trò : Kết nối vào Node-1, lấy file về lưu SPIFFS,
 *           rồi phát WiFi AP "ESP32-Node-2" để laptop lấy file
 *
 * BOOT button (GPIO 0):
 *   Nhấn 1 lần ngắn → Toggle ON/OFF
 *   ON  : AP bật, server chạy, LED sáng
 *   OFF : AP tắt, tiết kiệm điện, LED tắt
 *
 * Endpoints (port 80):
 *   GET  /status          ← trạng thái
 *   GET  /file/info       ← thông tin file trong SPIFFS
 *   GET  /file/list       ← danh sách tất cả file
 *   GET  /file/download   ← download file WAV
 *   POST /file/clear      ← xóa file SPIFFS
 *   POST /file/delete     ← xóa file theo tên
 *   GET  /ram/info        ← RAM buffer info
 *   POST /sync            ← trigger đồng bộ lại từ Node-1
 *
 * Port 8080: Raw TCP WAV (tương thích)
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <SPIFFS.h>
#include <vector>
#include "test_wav.h"

// ── Cấu hình Node-2 ───────────────────────────────────────────
#define NODE_ID            2

// AP của Node-2 (laptop kết nối vào đây để lấy file)
#define MY_AP_SSID         "ESP32-Node-2"
#define MY_AP_PASSWORD     "12345678"
#define MY_AP_CHANNEL      6          // Kênh khác Node-1 (channel 1)
#define MY_AP_HIDDEN       true       // Ẩn SSID cho demo
#define MY_AP_MAX_CON      4

// Node-1 cần kết nối để lấy file
#define NODE1_SSID         "ESP32-Node-1"
#define NODE1_PASSWORD     "12345678"
#define NODE1_IP           "192.168.4.1"
#define NODE1_TCP_PORT     8080
#define NODE1_HTTP_PORT    80

#define LED_PIN            2
#define BOOT_PIN           0              // Nút BOOT tích hợp (GPIO 0, active LOW)
#define HTTP_PORT          80
#define AUDIO_PORT         8080
#define SPIFFS_FILE        "/audio.wav"
#define MAX_FILE_SIZE      1800000   // 1.8 MB (no_ota: 1.875 MB SPIFFS)

// IP của AP Node-2
#define MY_AP_IP_STR       "192.168.5.1"

WebServer  server(HTTP_PORT);
WiFiServer audioServer(AUDIO_PORT);

// ── RAM buffer ────────────────────────────────────────────────
uint8_t* ramBuf   = nullptr;
size_t   ramSize  = 0;
bool     ramReady = false;

// ── Sync state ────────────────────────────────────────────────
bool syncDone   = false;
bool syncFailed = false;
String syncMsg  = "not started";

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
  WiFi.mode(WIFI_AP_STA);
  IPAddress apIP(192,168,5,1);
  IPAddress gw(192,168,5,1);
  IPAddress sn(255,255,255,0);
  WiFi.softAPConfig(apIP,gw,sn);
  WiFi.softAP(MY_AP_SSID,MY_AP_PASSWORD,MY_AP_CHANNEL,MY_AP_HIDDEN,MY_AP_MAX_CON);
  server.begin();
  audioServer.begin();
  digitalWrite(LED_PIN, HIGH);
  Serial.println("[BOOT] Node-2 ENABLED — AP ON");
  blinkLED(3, 80);
}

void disableNode() {
  nodeEnabled = false;
  WiFi.softAPdisconnect(true);   // Tắt AP, ngắt tất cả client
  WiFi.disconnect(true);         // Tắt STA
  WiFi.mode(WIFI_OFF);
  digitalWrite(LED_PIN, LOW);
  Serial.println("[BOOT] Node-2 DISABLED — AP OFF (low power)");
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
bool spiffsHasFile() { return SPIFFS.exists(SPIFFS_FILE); }

size_t spiffsFileSize() {
  if (!spiffsHasFile()) return 0;
  File f = SPIFFS.open(SPIFFS_FILE,"r");
  if (!f) return 0;
  size_t sz=f.size(); f.close(); return sz;
}

bool spiffsSave(const uint8_t* buf, size_t size) {
  // Kiểm tra dung lượng SPIFFS trước khi ghi
  size_t freeBytes = SPIFFS.totalBytes() - SPIFFS.usedBytes();
  if (size > freeBytes) {
    Serial.printf("[SPIFFS] Not enough space: need %d, free %d bytes\n", size, freeBytes);
    return false;
  }
  File f = SPIFFS.open(SPIFFS_FILE,"w");
  if (!f) { Serial.println("[SPIFFS] Write FAILED"); return false; }
  size_t wr = f.write(buf,size); f.close();
  bool ok=(wr==size);
  if (!ok) {
    SPIFFS.remove(SPIFFS_FILE);
    Serial.printf("[SPIFFS] Save FAILED (%d/%d) — removed empty entry\n", wr, size);
  } else {
    Serial.printf("[SPIFFS] Save %d/%d bytes → OK\n", wr, size);
  }
  return ok;
}

// Lưu vào SPIFFS với path tùy ý
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
    Serial.printf("[SPIFFS] SaveAs '%s' FAILED (%d/%d) — removed empty entry\n", path.c_str(), wr, size);
  } else {
    Serial.printf("[SPIFFS] SaveAs '%s' %d/%d → OK\n", path.c_str(), wr, size);
  }
  return ok;
}

// Sanitize tên file: chỉ giữ ký tự an toàn
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

static uint16_t _fileCounter = 0;
String genAutoFilename() {
  _fileCounter++;
  char buf[24];
  snprintf(buf, sizeof(buf), "audio_%04d.wav", _fileCounter);
  return String(buf);
}

bool spiffsLoad() {
  if (!spiffsHasFile()) return false;
  File f = SPIFFS.open(SPIFFS_FILE,"r");
  if (!f) return false;
  size_t sz=f.size();
  if (sz==0||sz>MAX_FILE_SIZE){f.close();return false;}
  if (ramBuf){free(ramBuf);ramBuf=nullptr;ramSize=0;}
  ramBuf=(uint8_t*)malloc(sz);
  if (!ramBuf){f.close();Serial.println("[SPIFFS] OOM");return false;}
  size_t rd=f.read(ramBuf,sz); f.close();
  ramSize=rd; ramReady=(rd>=44);
  Serial.printf("[SPIFFS] Load %d bytes → %s\n",rd,ramReady?"OK":"FAIL");
  return ramReady;
}

// ── Format ────────────────────────────────────────────────────
String formatUptime(uint32_t ms) {
  uint32_t s=ms/1000,m=s/60; s%=60; uint32_t h=m/60; m%=60;
  char b[32]; snprintf(b,sizeof(b),"%02d:%02d:%02d",h,m,s); return String(b);
}

String wavInfoJson(const uint8_t* buf, size_t size) {
  if (!buf||size<44) return "{}";
  if (buf[0]!='R'||buf[1]!='I'||buf[2]!='F'||buf[3]!='F') return "{\"is_wav\":false}";
  if (buf[8]!='W'||buf[9]!='A'||buf[10]!='V'||buf[11]!='E') return "{\"is_wav\":false}";
  uint16_t fmt=buf[20]|(buf[21]<<8);
  uint16_t ch=buf[22]|(buf[23]<<8);
  uint32_t sr=buf[24]|(buf[25]<<8)|(buf[26]<<16)|(buf[27]<<24);
  uint16_t bps=buf[34]|(buf[35]<<8);
  uint32_t dsz=buf[40]|(buf[41]<<8)|(buf[42]<<16)|(buf[43]<<24);
  float dur=(sr>0&&ch>0&&bps>0)?(float)dsz/(sr*ch*(bps/8)):0.0f;
  String j="{\"is_wav\":true";
  j+=",\"format\":\""+String(fmt==1?"PCM":fmt==3?"FLOAT":"OTHER")+"\"";
  j+=",\"channels\":"+String(ch);
  j+=",\"sample_rate\":"+String(sr);
  j+=",\"bits_per_sample\":"+String(bps);
  j+=",\"data_size\":"+String(dsz);
  j+=",\"duration_sec\":"+String(dur,2)+"}";
  return j;
}

// ── Helper: HTTP GET text từ Node-1 ──────────────────────────
String httpGetFromNode1(const char* path, int timeoutMs = 5000) {
  WiFiClient c;
  if (!c.connect(NODE1_IP, NODE1_HTTP_PORT)) return "";
  c.printf("GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n", path, NODE1_IP);
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

// ── Helper: HTTP GET file binary từ Node-1 → lưu SPIFFS ──────
bool httpDownloadFileFromNode1(const String& filename) {
  String path = "/file/download?name=" + filename;
  WiFiClient c;
  if (!c.connect(NODE1_IP, NODE1_HTTP_PORT)) {
    Serial.printf("[Sync] HTTP connect FAILED for '%s'\n", filename.c_str());
    return false;
  }
  c.printf("GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n",
           path.c_str(), NODE1_IP);
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
    Serial.printf("[Sync] Bad CL=%d for '%s'\n", contentLength, filename.c_str());
    return false;
  }
  uint8_t* buf = (uint8_t*)malloc(contentLength);
  if (!buf) { c.stop(); Serial.println("[Sync] OOM"); return false; }
  size_t rx = 0; t = millis();
  while (rx < (size_t)contentLength && c.connected() && (millis()-t) < 20000) {
    size_t av = c.available();
    if (av > 0) {
      size_t ch = min(av, (size_t)(contentLength-rx));
      c.readBytes(buf+rx, ch); rx += ch; t = millis();
    } else delay(1);
  }
  c.stop();
  if (rx < 44) { free(buf); Serial.printf("[Sync] Incomplete '%s'\n", filename.c_str()); return false; }
  bool saved = spiffsSaveAs(buf, rx, "/" + filename);
  // Nếu là audio.wav → cập nhật RAM buffer (file chính)
  if (saved && filename == "audio.wav") {
    if (ramBuf) { free(ramBuf); ramBuf = nullptr; ramSize = 0; ramReady = false; }
    ramBuf = buf; ramSize = rx; ramReady = true;
  } else {
    free(buf);
  }
  Serial.printf("[Sync] '%s' %d bytes → %s\n", filename.c_str(), rx, saved?"OK":"FAIL");
  return saved;
}

// ── Đồng bộ TẤT CẢ file từ Node-1 (STA mode) ────────────────
bool syncFromNode1() {
  Serial.println("\n[Sync] Connecting to Node-1...");
  syncMsg = "connecting";

  WiFi.begin(NODE1_SSID, NODE1_PASSWORD);
  int retries = 0;
  while (WiFi.status() != WL_CONNECTED && retries < 12) {
    // Serve AP clients trong lúc chờ STA kết nối
    unsigned long tw = millis();
    while (millis() - tw < 300) {
      server.handleClient();
      delay(5);
    }
    Serial.print("."); retries++;
  }
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\n[Sync] FAILED: Node-1 not found");
    syncMsg = "failed: Node-1 not found";
    WiFi.disconnect(false);
    return false;
  }
  Serial.printf("\n[Sync] Connected to Node-1. STA IP: %s\n",
                WiFi.localIP().toString().c_str());
  delay(500);

  // Lấy danh sách file từ Node-1
  String listJson = httpGetFromNode1("/file/list", 6000);
  Serial.printf("[Sync] /file/list: %s\n", listJson.substring(0,200).c_str());

  // Nếu Node-1 chưa có file → skip
  if (listJson.indexOf("\"count\":0") >= 0 || listJson.indexOf("\"files\":[]") >= 0) {
    Serial.println("[Sync] Node-1 has no files — skip");
    WiFi.disconnect(false); delay(300);
    syncMsg = "ok: node-1 empty";
    return false;
  }

  // Parse tên file từ JSON: tìm "name":"<filename>"
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
  Serial.printf("[Sync] Node-1 has %d file(s)\n", remoteFiles.size());

  if (remoteFiles.empty()) {
    // Fallback: nếu /file/list rỗng → thử lấy audio.wav qua TCP (backward compat)
    WiFiClient tcp;
    if (tcp.connect(NODE1_IP, NODE1_TCP_PORT)) {
      tcp.printf("GET /audio.wav HTTP/1.1\r\nHost: %s:%d\r\nConnection: close\r\n\r\n",
                 NODE1_IP, NODE1_TCP_PORT);
      int clen = 0; unsigned long t = millis();
      while (tcp.connected() && (millis()-t) < 5000) {
        String line = tcp.readStringUntil('\n'); line.trim();
        if (line.length()==0) break;
        String lo=line; lo.toLowerCase();
        if (lo.startsWith("content-length:")) clen=line.substring(line.indexOf(':')+1).toInt();
        t=millis();
      }
      if (clen > 0 && clen <= (int)MAX_FILE_SIZE) {
        if (ramBuf){free(ramBuf);ramBuf=nullptr;ramSize=0;}
        ramBuf=(uint8_t*)malloc(clen);
        if (ramBuf) {
          size_t rx=0; t=millis();
          while(rx<(size_t)clen&&tcp.connected()&&(millis()-t)<20000){
            size_t av=tcp.available();
            if(av>0){size_t ch=min(av,(size_t)(clen-rx));tcp.readBytes(ramBuf+rx,ch);rx+=ch;t=millis();}else delay(1);
          }
          ramSize=rx; ramReady=(rx>=44);
          if (ramReady) spiffsSave(ramBuf, ramSize);
        }
      }
      tcp.stop();
    }
    WiFi.disconnect(false); delay(500);
    syncMsg = ramReady ? "ok: fallback audio.wav" : "failed: no files";
    return ramReady;
  }

  // Download từng file chưa có trong SPIFFS
  int downloaded = 0;
  for (auto& fname : remoteFiles) {
    String path = "/" + fname;
    if (SPIFFS.exists(path)) {
      Serial.printf("[Sync] Skip '%s' — already exists\n", fname.c_str());
      continue;
    }
    bool ok = httpDownloadFileFromNode1(fname);
    if (ok) downloaded++;
    delay(100);
  }

  // Xóa file local không còn tồn tại ở Node-1
  int deleted = 0;
  {
    std::vector<String> localFiles;
    File root = SPIFFS.open("/");
    File fi = root.openNextFile();
    while (fi) {
      if (!fi.isDirectory()) localFiles.push_back(String(fi.name()));
      fi.close();
      fi = root.openNextFile();
    }
    root.close();
    for (auto& lname : localFiles) {
      String displayName = lname.startsWith("/") ? lname.substring(1) : lname;
      bool foundInRemote = false;
      for (auto& rname : remoteFiles) {
        if (rname == displayName) { foundInRemote = true; break; }
      }
      if (!foundInRemote) {
        String lpath = lname.startsWith("/") ? lname : ("/" + lname);
        SPIFFS.remove(lpath);
        Serial.printf("[Sync] Deleted local '%s' — not in Node-1\n", displayName.c_str());
        deleted++;
      }
    }
  }
  Serial.printf("[Sync] +%d downloaded, -%d deleted\n", downloaded, deleted);

  WiFi.disconnect(false); delay(300);
  Serial.println("[Sync] STA disconnected. AP still running.");
  Serial.printf("[Sync] Downloaded %d/%d file(s)\n", downloaded, remoteFiles.size());

  // Đảm bảo ramBuf có file chính (audio.wav hoặc file đầu tiên)
  if (!ramReady) {
    String firstFile = "/" + remoteFiles[0];
    if (SPIFFS.exists(firstFile)) {
      File f = SPIFFS.open(firstFile, "r");
      if (f) {
        size_t sz = f.size();
        if (ramBuf){free(ramBuf);ramBuf=nullptr;}
        ramBuf=(uint8_t*)malloc(sz);
        if (ramBuf){size_t rd=f.read(ramBuf,sz);f.close();ramSize=rd;ramReady=(rd>=44);}
        else f.close();
      }
    }
  }

  syncMsg = "ok: synced " + String(downloaded) + "/" + String(remoteFiles.size()) + " files";
  return (downloaded > 0 || SPIFFS.usedBytes() > 0);
}

// ── HTTP Handlers ─────────────────────────────────────────────

void handleStatus() {
  server.send(200,"application/json",
    String("{\"node\":2") +
    ",\"ap_ssid\":\"" + MY_AP_SSID + "\"" +
    ",\"ap_ip\":\"" + MY_AP_IP_STR + "\"" +
    ",\"uptime\":\"" + formatUptime(millis()) + "\"" +
    ",\"free_heap\":" + String(ESP.getFreeHeap()) +
    ",\"sync_done\":" + (syncDone?"true":"false") +
    ",\"sync_msg\":\"" + syncMsg + "\"" +
    ",\"spiffs_has_file\":" + (spiffsHasFile()?"true":"false") +
    ",\"spiffs_size\":" + String(spiffsFileSize()) +
    ",\"ram_ready\":" + (ramReady?"true":"false") +
    ",\"ram_size\":" + String(ramSize) +
    ",\"node_enabled\":" + (nodeEnabled?"true":"false") +
    ",\"builtin_wav_size\":" + String(TEST_WAV_SIZE) + "}");
}

void handleFileInfo() {
  bool has=spiffsHasFile(); size_t sz=spiffsFileSize();
  String j="{\"has_file\":"+String(has?"true":"false");
  j+=",\"path\":\""+String(SPIFFS_FILE)+"\"";
  j+=",\"size\":"+String(sz);
  j+=",\"size_kb\":"+String(sz/1024.0f,1);
  if (has&&ramReady&&ramBuf) j+=",\"wav_info\":"+wavInfoJson(ramBuf,ramSize);
  j+=",\"sync_done\":"+String(syncDone?"true":"false");
  j+=",\"sync_msg\":\""+syncMsg+"\"";
  j+=",\"free_heap\":"+String(ESP.getFreeHeap())+"}";
  server.send(200,"application/json",j);
}

void handleFileDownload() {
  // Ưu tiên: SPIFFS → RAM → builtin
  if (spiffsHasFile()) {
    File f=SPIFFS.open(SPIFFS_FILE,"r");
    if (f) {
      size_t sz=f.size();
      server.sendHeader("Content-Disposition","attachment; filename=audio.wav");
      server.sendHeader("Content-Length",String(sz));
      server.setContentLength(sz);
      server.send(200,"audio/wav","");
      uint8_t buf[1024]; size_t sent=0;
      while(sent<sz){size_t rd=f.read(buf,min((size_t)1024,sz-sent));server.sendContent((char*)buf,rd);sent+=rd;}
      f.close();
      Serial.printf("[Download] Sent %d bytes from SPIFFS\n",sz);
      blinkLED(3,100); return;
    }
  }
  if (ramReady&&ramSize>0){
    server.sendHeader("Content-Disposition","attachment; filename=audio.wav");
    server.send_P(200,"audio/wav",(const char*)ramBuf,ramSize);
    blinkLED(3,100); return;
  }
  if (TEST_WAV_SIZE>0){
    server.sendHeader("Content-Disposition","attachment; filename=audio.wav");
    server.send_P(200,"audio/wav",(const char*)TEST_WAV_DATA,TEST_WAV_SIZE);
    blinkLED(3,100); return;
  }
  server.send(404,"application/json","{\"error\":\"no file\"}");
}

void handleFileClear() {
  bool ok=SPIFFS.remove(SPIFFS_FILE);
  if (ramBuf){free(ramBuf);ramBuf=nullptr;ramSize=0;ramReady=false;}
  syncDone=false; syncMsg="cleared";
  server.send(200,"application/json",
    ok?"{\"status\":\"ok\",\"message\":\"File da xoa\"}":
       "{\"status\":\"ok\",\"message\":\"Khong co file de xoa\"}");
}

// GET /file/list — liệt kê TẤT CẢ file trong SPIFFS (tương thích Node-1)
void handleFileList() {
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
  String j = "{\"files\":[";
  int count = 0;
  for (auto& fname : names) {
    String path = fname.startsWith("/") ? fname : ("/" + fname);
    String displayName = fname.startsWith("/") ? fname.substring(1) : fname;
    File f2 = SPIFFS.open(path, "r");
    size_t sz = f2 ? f2.size() : 0;
    if (sz == 0 && ramReady && ramSize > 0 && path == String(SPIFFS_FILE)) sz = ramSize;
    float dur = 0.0f;
    if (sz >= 44) {
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
    if (count>0) j+=",";
    char sz_kb[16]; snprintf(sz_kb,sizeof(sz_kb),"%.1f KB",sz/1024.0f);
    j+="{\"name\":\""+displayName+"\"";
    j+=",\"path\":\""+path+"\"";
    j+=",\"size\":"+String(sz);
    j+=",\"size_kb\":\""+String(sz_kb)+"\"";
    j+=",\"duration_sec\":"+String(dur,2)+"}";
    count++;
  }
  j+="],\"count\":"+String(count);
  j+=",\"spiffs_total\":"+String(SPIFFS.totalBytes());
  j+=",\"spiffs_used\":"+String(SPIFFS.usedBytes());
  j+=",\"spiffs_free\":"+String(SPIFFS.totalBytes()-SPIFFS.usedBytes())+"}";
  server.send(200,"application/json",j);
}

// POST /file/delete?name=<filename> — xóa file đúng theo tên
void handleFileDelete() {
  String name = server.arg("name");
  name.trim();
  if (name.length()==0) {
    server.send(400,"application/json","{\"error\":\"missing name\"}");
    return;
  }
  String safe = sanitizeFilename(name);
  if (safe.length()==0) { server.send(400,"application/json","{\"error\":\"invalid name\"}"); return; }
  String path = "/" + safe;
  if (!SPIFFS.exists(path)) {
    server.send(404,"application/json","{\"error\":\"file not found\"}");
    return;
  }
  bool ok = SPIFFS.remove(path);
  if (ok && path == String(SPIFFS_FILE)) {
    if (ramBuf){free(ramBuf);ramBuf=nullptr;ramSize=0;ramReady=false;}
    syncDone=false; syncMsg="deleted";
  }
  server.send(ok?200:500,"application/json",
    ok?"{\"status\":\"ok\"}":"{\"error\":\"delete failed\"}");
  Serial.printf("[SPIFFS] Delete '%s' → %s\n", path.c_str(), ok?"OK":"FAIL");
}

void handleRamInfo() {
  if (!ramReady||ramSize<44) {
    server.send(200,"application/json",
      String("{\"ram_ready\":false,\"free_heap\":")+String(ESP.getFreeHeap())+
      ",\"spiffs_has_file\":"+String(spiffsHasFile()?"true":"false")+
      ",\"sync_msg\":\""+syncMsg+"\"}");
    return;
  }
  char magic[5]={0}; memcpy(magic,ramBuf,4);
  String j="{\"ram_ready\":true,\"size_bytes\":"+String(ramSize);
  j+=",\"magic\":\""+String(magic)+"\"";
  j+=",\"wav_info\":"+wavInfoJson(ramBuf,ramSize);
  j+=",\"sync_msg\":\""+syncMsg+"\"";
  j+=",\"free_heap\":"+String(ESP.getFreeHeap())+"}";
  server.send(200,"application/json",j);
}

// POST /sync — trigger đồng bộ lại từ Node-1
void handleSync() {
  server.send(200,"application/json",
    "{\"status\":\"ok\",\"message\":\"Sync starting in background\"}");
  syncDone = syncFromNode1();
  syncFailed = !syncDone;
  if (syncDone) blinkLED(5,80);
  else blinkLED(2,500);
}

void handleNotFound() {
  server.send(404,"application/json","{\"error\":\"not found\"}");
}

// ── Raw TCP port 8080 ─────────────────────────────────────────
void handleRawTCP(WiFiClient& client) {
  String req=client.readStringUntil('\n'); req.trim();
  int clen=0;
  String xFilename="";
  while(client.connected()){
    String line=client.readStringUntil('\n');line.trim();
    if(line.length()==0)break;
    String lo=line;lo.toLowerCase();
    if(lo.startsWith("content-length:"))clen=line.substring(line.indexOf(':')+1).toInt();
    if(lo.startsWith("x-filename:")){xFilename=line.substring(line.indexOf(':')+1);xFilename.trim();}
  }
  if(req.startsWith("GET")){
    if(spiffsHasFile()){
      File f=SPIFFS.open(SPIFFS_FILE,"r");
      if(f){
        size_t sz=f.size();
        client.printf("HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\nContent-Length: %d\r\n"
                      "Content-Disposition: attachment; filename=audio.wav\r\nConnection: close\r\n\r\n",sz);
        uint8_t buf[1024];size_t sent=0;
        while(sent<sz&&client.connected()){size_t rd=f.read(buf,min((size_t)1024,sz-sent));client.write(buf,rd);sent+=rd;}
        f.close();client.flush();blinkLED(3,100);return;
      }
    }
    const uint8_t* buf=(ramReady&&ramSize>0)?ramBuf:TEST_WAV_DATA;
    size_t sz=(ramReady&&ramSize>0)?ramSize:TEST_WAV_SIZE;
    if(sz==0){client.print("HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n");return;}
    client.printf("HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\nContent-Length: %d\r\n"
                  "Content-Disposition: attachment; filename=audio.wav\r\nConnection: close\r\n\r\n",sz);
    size_t sent=0;while(sent<sz&&client.connected()){size_t ch=min((size_t)1024,sz-sent);client.write(buf+sent,ch);sent+=ch;}
    client.flush();blinkLED(3,100);
  } else if(req.startsWith("POST")){
    if(clen<=0||clen>(int)MAX_FILE_SIZE){client.print("HTTP/1.1 400\r\nConnection: close\r\n\r\n");return;}
    if(ramBuf){free(ramBuf);ramBuf=nullptr;ramSize=0;}
    ramBuf=(uint8_t*)malloc(clen);
    if(!ramBuf){client.print("HTTP/1.1 507\r\nConnection: close\r\n\r\n");return;}
    size_t rx=0;unsigned long t=millis();
    while(rx<(size_t)clen&&client.connected()&&(millis()-t)<20000){
      size_t av=client.available();
      if(av>0){size_t ch=min(av,(size_t)(clen-rx));client.readBytes(ramBuf+rx,ch);rx+=ch;t=millis();}else delay(1);
    }
    ramSize=rx;ramReady=(rx>=44);
    if(ramReady){
      String saveAs = sanitizeFilename(xFilename);
      if (saveAs.length() == 0) saveAs = genAutoFilename();
      bool sv = spiffsSaveAs(ramBuf, ramSize, "/" + saveAs);
      String r="{\"status\":\"ok\",\"received\":"+String(rx)+
               ",\"filename\":\""+saveAs+"\""+
               ",\"spiffs_saved\":"+String(sv?"true":"false")+"}";
      client.printf("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s",r.length(),r.c_str());blinkLED(5,80);
    } else client.print("HTTP/1.1 400\r\nConnection: close\r\n\r\n{\"error\":\"incomplete\"}");
  }
}

// ── Setup ─────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200); delay(500);
  pinMode(LED_PIN,OUTPUT); digitalWrite(LED_PIN,LOW);
  pinMode(BOOT_PIN, INPUT_PULLUP);  // Nút BOOT GPIO 0, active LOW

  Serial.println("\n══════════════════════════════");
  Serial.println(" ESP32 NODE-2  (APSTA + SPIFFS)");
  Serial.println("══════════════════════════════");

  // Init SPIFFS
  if (!SPIFFS.begin(true)) { SPIFFS.format(); SPIFFS.begin(true); }
  Serial.printf("[SPIFFS] Total:%d Used:%d bytes\n",
    SPIFFS.totalBytes(),SPIFFS.usedBytes());

  // Bật WiFi APSTA — vừa phát AP vừa kết nối STA
  WiFi.mode(WIFI_AP_STA);
  IPAddress apIP(192,168,5,1);
  IPAddress gw(192,168,5,1);
  IPAddress sn(255,255,255,0);
  WiFi.softAPConfig(apIP,gw,sn);
  WiFi.softAP(MY_AP_SSID,MY_AP_PASSWORD,MY_AP_CHANNEL,MY_AP_HIDDEN,MY_AP_MAX_CON);
  delay(200);
  Serial.printf("[AP] SSID: %s  IP: %s\n",MY_AP_SSID,WiFi.softAPIP().toString().c_str());
  digitalWrite(LED_PIN,HIGH);

  // Khởi động HTTP server và TCP server
  server.on("/status",        HTTP_GET,  handleStatus);
  server.on("/file/info",     HTTP_GET,  handleFileInfo);
  server.on("/file/list",     HTTP_GET,  handleFileList);
  server.on("/file/download", HTTP_GET,  handleFileDownload);
  server.on("/file/clear",    HTTP_POST, handleFileClear);
  server.on("/file/delete",   HTTP_POST, handleFileDelete);
  server.on("/ram/info",      HTTP_GET,  handleRamInfo);
  server.on("/sync",          HTTP_POST, handleSync);
  // Tương thích firmware cũ
  server.on("/audio/info",    HTTP_GET,  handleFileInfo);
  server.on("/ram/clear",     HTTP_POST, handleFileClear);
  server.onNotFound(handleNotFound);
  server.begin();
  audioServer.begin();

  // Nếu SPIFFS đã có file từ lần trước → load vào RAM
  if (spiffsHasFile()) {
    Serial.printf("[SPIFFS] Found existing file: %d bytes\n",spiffsFileSize());
    spiffsLoad();
    if (ramReady) {
      syncDone=true; syncMsg="loaded from SPIFFS (previous session)";
      Serial.println("[SPIFFS] File loaded — skip sync");
      blinkLED(5,80);
      Serial.println("[Ready] Node-2 serving existing file");
      return;
    }
  }

  // Chưa có file → thử sync từ Node-1
  Serial.println("\n[Boot] No SPIFFS file — trying sync from Node-1...");
  blinkLED(2,200);
  delay(1000);

  syncDone = syncFromNode1();
  syncFailed = !syncDone;

  if (syncDone) {
    Serial.printf("[Boot] Sync OK: %d bytes\n",ramSize);
    blinkLED(5,80);
  } else {
    Serial.println("[Boot] Sync FAILED — using built-in WAV");
    syncMsg = "failed: using builtin";
    blinkLED(3,300);
  }

  Serial.println("\n[Ready] Node-2 Endpoints:");
  Serial.printf("  Connect WiFi: %s / %s\n",MY_AP_SSID,MY_AP_PASSWORD);
  Serial.printf("  GET  http://%s/status\n",MY_AP_IP_STR);
  Serial.printf("  GET  http://%s/file/info\n",MY_AP_IP_STR);
  Serial.printf("  GET  http://%s/file/download  <- download file\n",MY_AP_IP_STR);
  Serial.printf("  POST http://%s/sync           <- re-sync tu Node-1\n",MY_AP_IP_STR);
  Serial.printf("  GET  http://%s:8080/audio.wav <- TCP download\n",MY_AP_IP_STR);
  Serial.println("[BOOT] Nhan nut BOOT de bat/tat WiFi AP");
}

// ── Loop ──────────────────────────────────────────────────────
static unsigned long _lastSync1 = 0;
static bool _syncing = false;
#define SYNC_INTERVAL_MS 60000UL  // 60 giây (tránh drop AP quá thường xuyên)

void loop() {
  // Luôn kiểm tra nút BOOT để toggle bật/tắt
  checkBootButton();

  // Nếu node đang bị tắt → không xử lý server / sync
  if (!nodeEnabled) {
    delay(10);
    return;
  }

  server.handleClient();
  WiFiClient c=audioServer.accept();
  if(c){
    unsigned long t=millis();
    while(!c.available()&&c.connected()&&(millis()-t)<3000)delay(1);
    if(c.available())handleRawTCP(c);
    c.stop();
    // Có TCP client vừa kết nối → reset timer
    _lastSync1 = millis();
  }
  // Periodic sync từ Node-1 mỗi 60s
  if (!_syncing && (millis() - _lastSync1 >= SYNC_INTERVAL_MS)) {
    _syncing = true;
    _lastSync1 = millis();
    syncFromNode1();
    _syncing = false;
  }
}
