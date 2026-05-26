#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <SPI.h>
#include <SD.h>
#include <FS.h>

// ================== microSD PIN ==================
#define SD_CS    3
#define SD_SCK   23
#define SD_MOSI  24
#define SD_MISO  25
#define SD_FORMAT_IF_EMPTY true

SPIClass spiSD(FSPI);

// ================== WIFI AP CONFIG ==================
// Giữ đúng SSID/password theo code Python client cũ
const char* AP_SSID = "7068616e746f6d303030303030300002";
const char* AP_PASS = "12345678";

// Cho giống server cũ: http://10.42.0.1:8765
IPAddress local_IP(10, 42, 0, 1);
IPAddress gateway(10, 42, 0, 1);
IPAddress subnet(255, 255, 255, 0);

#define SERVER_PORT 8765
WebServer server(SERVER_PORT);

// ================== ONBOARD LED ==================
#ifndef LED_BUILTIN
#define LED_BUILTIN 8
#endif

const int STATUS_LED_PIN = LED_BUILTIN;
const uint8_t BLINK_COUNT_PER_UPLOAD = 3;
const uint16_t BLINK_INTERVAL_MS = 120;
bool ledState = false;
bool blinkActive = false;
uint8_t blinkTogglesLeft = 0;
unsigned long lastBlinkMs = 0;

// ================== UPLOAD STATE ==================
File uploadFile;
String uploadFileName = "";
String uploadFilePath = "";
bool uploadOK = false;
String uploadError = "";
uint64_t uploadBytes = 0;

// ================== UTILS ==================
String humanSize(uint64_t bytes) {
  if (bytes < 1024) return String((unsigned long long)bytes) + " B";
  if (bytes < 1024ULL * 1024ULL) return String((double)bytes / 1024.0, 1) + " KB";
  return String((double)bytes / 1024.0 / 1024.0, 2) + " MB";
}

String jsonEscape(const String& s) {
  String out = "";
  for (size_t i = 0; i < s.length(); i++) {
    char c = s[i];
    if (c == '"') out += "\\\"";
    else if (c == '\\') out += "\\\\";
    else if (c == '\n') out += "\\n";
    else if (c == '\r') out += "\\r";
    else out += c;
  }
  return out;
}

String safeFileName(String name) {
  name.replace("\\", "/");

  int slash = name.lastIndexOf('/');
  if (slash >= 0) {
    name = name.substring(slash + 1);
  }

  String safe = "";
  for (size_t i = 0; i < name.length(); i++) {
    char c = name[i];

    if (
      (c >= 'a' && c <= 'z') ||
      (c >= 'A' && c <= 'Z') ||
      (c >= '0' && c <= '9') ||
      c == '.' || c == '_' || c == '-'
    ) {
      safe += c;
    } else {
      safe += "_";
    }
  }

  if (safe.length() == 0) {
    safe = "upload.bin";
  }

  if (safe.length() > 80) {
    safe = safe.substring(safe.length() - 80);
  }

  return safe;
}

bool initSDWithFallback() {
  const uint32_t freqs[] = {1000000, 400000, 8000000};

  for (size_t i = 0; i < (sizeof(freqs) / sizeof(freqs[0])); i++) {
    uint32_t hz = freqs[i];

    SD.end();
    Serial.printf("[SD] Thu SD.begin o %lu Hz...\n", (unsigned long)hz);

    if (SD.begin(SD_CS, spiSD, hz, "/sd", 8, SD_FORMAT_IF_EMPTY)) {
      Serial.printf("[SD] OK: Mount SD thanh cong o %lu Hz\n", (unsigned long)hz);
      return true;
    }
  }

  return false;
}

bool ensureUploadDir() {
  if (!SD.exists("/uploads")) {
    return SD.mkdir("/uploads");
  }
  return true;
}

uint64_t getUsedBytes() {
  return SD.usedBytes();
}

uint64_t getTotalBytes() {
  return SD.totalBytes();
}

void startUploadBlink() {
  blinkActive = true;
  blinkTogglesLeft = BLINK_COUNT_PER_UPLOAD * 2;
  lastBlinkMs = millis();
}

void updateStatusLed() {
  if (!blinkActive) return;

  unsigned long now = millis();
  if (now - lastBlinkMs < BLINK_INTERVAL_MS) return;

  lastBlinkMs = now;
  ledState = !ledState;
  digitalWrite(STATUS_LED_PIN, ledState ? HIGH : LOW);

  if (blinkTogglesLeft > 0) {
    blinkTogglesLeft--;
  }

  if (blinkTogglesLeft == 0) {
    blinkActive = false;
    ledState = false;
    digitalWrite(STATUS_LED_PIN, LOW);
  }
}

// ================== API HANDLERS ==================
void handleRoot() {
  String msg = "";
  msg += "ESP32-C5 microSD Upload Server\n";
  msg += "AP SSID: ";
  msg += AP_SSID;
  msg += "\n";
  msg += "IP: ";
  msg += WiFi.softAPIP().toString();
  msg += "\n";
  msg += "Port: ";
  msg += String(SERVER_PORT);
  msg += "\n\n";
  msg += "API:\n";
  msg += "GET  /api/status\n";
  msg += "GET  /api/filelist\n";
  msg += "POST /api/upload    form-data key=file\n";
  msg += "GET  /api/download?name=filename.bin\n";
  msg += "GET  /api/delete?name=filename.bin\n";
  msg += "POST /api/delete?name=filename.bin\n";
  msg += "POST /api/delete-all\n";
  msg += "DELETE /api/delete-all\n";

  server.send(200, "text/plain", msg);
}

void handleStatus() {
  uint8_t cardType = SD.cardType();

  String card = "UNKNOWN";
  if (cardType == CARD_NONE) card = "NONE";
  else if (cardType == CARD_MMC) card = "MMC";
  else if (cardType == CARD_SD) card = "SDSC";
  else if (cardType == CARD_SDHC) card = "SDHC/SDXC";

  String json = "{";
  json += "\"ok\":true,";
  json += "\"wifi_mode\":\"AP\",";
  json += "\"ssid\":\"" + jsonEscape(String(AP_SSID)) + "\",";
  json += "\"ip\":\"" + WiFi.softAPIP().toString() + "\",";
  json += "\"port\":" + String(SERVER_PORT) + ",";
  json += "\"sd_card_type\":\"" + card + "\",";
  json += "\"sd_total\":" + String((unsigned long long)getTotalBytes()) + ",";
  json += "\"sd_used\":" + String((unsigned long long)getUsedBytes()) + ",";
  json += "\"sd_total_human\":\"" + humanSize(getTotalBytes()) + "\",";
  json += "\"sd_used_human\":\"" + humanSize(getUsedBytes()) + "\"";
  json += "}";

  server.send(200, "application/json", json);
}

void handleFileList() {
  File dir = SD.open("/uploads");

  if (!dir || !dir.isDirectory()) {
    server.send(500, "application/json", "{\"ok\":false,\"error\":\"cannot_open_uploads_dir\"}");
    return;
  }

  String json = "{";
  json += "\"ok\":true,";
  json += "\"dir\":\"/uploads\",";
  json += "\"files\":[";

  bool first = true;

  while (true) {
    File file = dir.openNextFile();
    if (!file) break;

    if (!file.isDirectory()) {
      String fullName = String(file.name());

      int slash = fullName.lastIndexOf('/');
      String name = fullName;
      if (slash >= 0) {
        name = fullName.substring(slash + 1);
      }

      uint64_t size = file.size();

      if (!first) json += ",";
      first = false;

      json += "{";
      json += "\"name\":\"" + jsonEscape(name) + "\",";
      json += "\"path\":\"/uploads/" + jsonEscape(name) + "\",";
      json += "\"size\":" + String((unsigned long long)size) + ",";
      json += "\"size_human\":\"" + humanSize(size) + "\",";
      json += "\"download\":\"/api/download?name=" + jsonEscape(name) + "\"";
      json += "}";
    }

    file.close();
  }

  dir.close();

  json += "]}";

  server.send(200, "application/json", json);
}

String getNameArg() {
  if (!server.hasArg("name")) return "";
  return safeFileName(server.arg("name"));
}

void handleDownload() {
  String name = getNameArg();

  if (name.length() == 0) {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"missing_name\"}");
    return;
  }

  String path = "/uploads/" + name;

  if (!SD.exists(path)) {
    server.send(404, "application/json", "{\"ok\":false,\"error\":\"file_not_found\"}");
    return;
  }

  File file = SD.open(path, FILE_READ);
  if (!file) {
    server.send(500, "application/json", "{\"ok\":false,\"error\":\"cannot_open_file\"}");
    return;
  }

  server.sendHeader("Content-Disposition", "attachment; filename=\"" + name + "\"");
  server.streamFile(file, "application/octet-stream");
  file.close();
}

void handleDelete() {
  String name = getNameArg();

  if (name.length() == 0) {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"missing_name\"}");
    return;
  }

  String path = "/uploads/" + name;

  if (!SD.exists(path)) {
    server.send(404, "application/json", "{\"ok\":false,\"error\":\"file_not_found\"}");
    return;
  }

  bool ok = SD.remove(path);

  if (ok) {
    String json = "{\"ok\":true,\"deleted\":\"" + jsonEscape(name) + "\"}";
    server.send(200, "application/json", json);
  } else {
    server.send(500, "application/json", "{\"ok\":false,\"error\":\"delete_failed\"}");
  }
}

void handleDeleteAll() {
  File dir = SD.open("/uploads");

  if (!dir || !dir.isDirectory()) {
    server.send(500, "application/json", "{\"ok\":false,\"error\":\"cannot_open_uploads_dir\"}");
    return;
  }

  uint32_t deleted = 0;
  uint32_t failed = 0;

  while (true) {
    File file = dir.openNextFile();
    if (!file) break;

    if (!file.isDirectory()) {
      String fullName = String(file.name());
      String name = fullName;

      int slash = fullName.lastIndexOf('/');
      if (slash >= 0) {
        name = fullName.substring(slash + 1);
      }

      file.close();

      String path = "/uploads/" + name;
      if (SD.remove(path)) {
        deleted++;
      } else {
        failed++;
      }
    } else {
      file.close();
    }
  }

  dir.close();

  String json = "{";
  json += "\"ok\":";
  json += (failed == 0 ? "true" : "false");
  json += ",";
  json += "\"deleted\":";
  json += String(deleted);
  json += ",";
  json += "\"failed\":";
  json += String(failed);
  json += "}";

  server.send(failed == 0 ? 200 : 500, "application/json", json);
}

void handleUploadResponse() {
  if (uploadOK) {
    String json = "{";
    json += "\"ok\":true,";
    json += "\"name\":\"" + jsonEscape(uploadFileName) + "\",";
    json += "\"path\":\"" + jsonEscape(uploadFilePath) + "\",";
    json += "\"size\":" + String((unsigned long long)uploadBytes) + ",";
    json += "\"size_human\":\"" + humanSize(uploadBytes) + "\"";
    json += "}";

    server.send(200, "application/json", json);
  } else {
    String json = "{";
    json += "\"ok\":false,";
    json += "\"error\":\"" + jsonEscape(uploadError) + "\"";
    json += "}";

    server.send(500, "application/json", json);
  }
}

void handleFileUpload() {
  HTTPUpload& upload = server.upload();

  if (upload.status == UPLOAD_FILE_START) {
    uploadOK = false;
    uploadError = "";
    uploadBytes = 0;

    uploadFileName = safeFileName(upload.filename);
    uploadFilePath = "/uploads/" + uploadFileName;

    Serial.printf("[UPLOAD] START name=%s path=%s\n", uploadFileName.c_str(), uploadFilePath.c_str());

    if (!ensureUploadDir()) {
      uploadError = "cannot_create_uploads_dir";
      Serial.println("[UPLOAD] FAIL cannot create /uploads");
      return;
    }

    if (SD.exists(uploadFilePath)) {
      SD.remove(uploadFilePath);
    }

    uploadFile = SD.open(uploadFilePath, FILE_WRITE);
    if (!uploadFile) {
      uploadError = "cannot_open_file_for_write";
      Serial.println("[UPLOAD] FAIL open file write");
      return;
    }
  }

  else if (upload.status == UPLOAD_FILE_WRITE) {
    if (uploadFile) {
      size_t written = uploadFile.write(upload.buf, upload.currentSize);
      uploadBytes += written;

      if (written != upload.currentSize) {
        uploadError = "sd_write_failed";
        Serial.printf("[UPLOAD] WRITE FAIL written=%u expected=%u\n",
                      (unsigned)written,
                      (unsigned)upload.currentSize);
      }
    } else {
      uploadError = "upload_file_not_open";
    }
  }

  else if (upload.status == UPLOAD_FILE_END) {
    if (uploadFile) {
      uploadFile.flush();
      uploadFile.close();
    }

    if (uploadError.length() == 0 && uploadBytes > 0) {
      uploadOK = true;
      startUploadBlink();
      Serial.printf("[UPLOAD] OK name=%s size=%llu\n",
                    uploadFileName.c_str(),
                    (unsigned long long)uploadBytes);
    } else {
      uploadOK = false;

      if (uploadError.length() == 0) {
        uploadError = "empty_upload";
      }

      if (uploadFilePath.length() > 0 && SD.exists(uploadFilePath)) {
        SD.remove(uploadFilePath);
      }

      Serial.printf("[UPLOAD] FAIL name=%s err=%s\n",
                    uploadFileName.c_str(),
                    uploadError.c_str());
    }
  }

  else if (upload.status == UPLOAD_FILE_ABORTED) {
    if (uploadFile) {
      uploadFile.close();
    }

    if (uploadFilePath.length() > 0 && SD.exists(uploadFilePath)) {
      SD.remove(uploadFilePath);
    }

    uploadOK = false;
    uploadError = "upload_aborted";

    Serial.printf("[UPLOAD] ABORTED name=%s\n", uploadFileName.c_str());
  }
}

void handleNotFound() {
  String json = "{";
  json += "\"ok\":false,";
  json += "\"error\":\"not_found\",";
  json += "\"uri\":\"" + jsonEscape(server.uri()) + "\"";
  json += "}";

  server.send(404, "application/json", json);
}

// ================== SETUP ==================
void setup() {
  Serial.begin(115200);
  delay(1500);

  Serial.println();
  Serial.println("=== ESP32-C5 microSD WIFI SERVER ===");

  Serial.println("[SD] Khoi tao SPI...");
  spiSD.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);

  Serial.println("[SD] Dang khoi tao the nho...");
  if (!initSDWithFallback()) {
    Serial.println("[SD] FAIL: Khong nhan duoc the nho!");
    Serial.println("Kiem tra day:");
    Serial.println("VCC  -> 3V3");
    Serial.println("GND  -> GND");
    Serial.println("SCK  -> GPIO23");
    Serial.println("MOSI -> GPIO24");
    Serial.println("MISO -> GPIO25");
    Serial.println("CS   -> GPIO3");
    while (true) {
      delay(1000);
    }
  }

  if (!ensureUploadDir()) {
    Serial.println("[SD] FAIL: Khong tao duoc /uploads");
    while (true) {
      delay(1000);
    }
  }

  Serial.println("[SD] OK");

  uint8_t cardType = SD.cardType();
  Serial.print("[SD] Loai the: ");
  if (cardType == CARD_MMC) Serial.println("MMC");
  else if (cardType == CARD_SD) Serial.println("SDSC");
  else if (cardType == CARD_SDHC) Serial.println("SDHC/SDXC");
  else Serial.println("UNKNOWN");

  Serial.printf("[SD] Total: %s\n", humanSize(SD.totalBytes()).c_str());
  Serial.printf("[SD] Used : %s\n", humanSize(SD.usedBytes()).c_str());

  Serial.println("[WIFI] Cau hinh IP AP...");
  WiFi.mode(WIFI_AP);
  WiFi.softAPConfig(local_IP, gateway, subnet);

  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, LOW);
  Serial.printf("[LED] STATUS PIN: GPIO%d\n", STATUS_LED_PIN);

  // Phát WiFi ẩn SSID
  bool hidden = true;
  int channel = 6;
  int max_connection = 4;

  bool apOK = WiFi.softAP(AP_SSID, AP_PASS, channel, hidden, max_connection);

  if (!apOK) {
    Serial.println("[WIFI] FAIL: Khong phat duoc WiFi AP");
    while (true) {
      delay(1000);
    }
  }

  Serial.println("[WIFI] OK: Da phat WiFi AP");
  Serial.printf("[WIFI] SSID: %s\n", AP_SSID);
  Serial.printf("[WIFI] PASS: %s\n", AP_PASS);
  Serial.printf("[WIFI] IP  : %s\n", WiFi.softAPIP().toString().c_str());
  Serial.printf("[HTTP] PORT: %d\n", SERVER_PORT);

  server.on("/", HTTP_GET, handleRoot);
  server.on("/api/status", HTTP_GET, handleStatus);
  server.on("/api/filelist", HTTP_GET, handleFileList);
  server.on("/api/download", HTTP_GET, handleDownload);

  server.on("/api/delete", HTTP_GET, handleDelete);
  server.on("/api/delete", HTTP_POST, handleDelete);
  server.on("/api/delete", HTTP_DELETE, handleDelete);
  server.on("/api/delete-all", HTTP_POST, handleDeleteAll);
  server.on("/api/delete-all", HTTP_DELETE, handleDeleteAll);

  server.on(
    "/api/upload",
    HTTP_POST,
    handleUploadResponse,
    handleFileUpload
  );

  server.onNotFound(handleNotFound);

  server.begin();

  Serial.println("[HTTP] Server started");
  Serial.println("=== READY ===");
}

// ================== LOOP ==================
void loop() {
  server.handleClient();
  updateStatusLed();
}
