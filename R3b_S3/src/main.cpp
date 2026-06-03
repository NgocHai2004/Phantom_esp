#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>
#include <SPI.h>
#include <SD.h>
#include <FS.h>

// ================== microSD PIN - ESP32-S3 ==================
#define SD_CS 10
#define SD_SCK 12
#define SD_MOSI 11
#define SD_MISO 13

#define SD_FORMAT_IF_EMPTY false

SPIClass spiSD(FSPI);

uint32_t mountedSDFreq = 0;

// ================== LED PIN - ESP32-S3 ==================
#define LED_RED 21
#define LED_GREEN 47
#define LED_WHITE 48

// ================== WIFI AP CONFIG ==================
const char *AP_SSID = "7068616e746f6d303030303030300002";
const char *AP_PASS = "12345678";

IPAddress local_IP(10, 42, 0, 1);
IPAddress gateway(10, 42, 0, 1);
IPAddress subnet(255, 255, 255, 0);

#define SERVER_PORT 8765
WebServer server(SERVER_PORT);

// ================== TRUSTED WIFI CONFIG ==================
#define TRUSTED_SSID "7068616e746f6d303030303030300001"
#define TRUSTED_PASS "12345678"
#define SCAN_INTERVAL_MS 10000UL
#define STA_CONNECT_TIMEOUT_MS 10000UL
#define AP_CHANNEL 1
#define AP_HIDDEN true
#define AP_MAX_CONN 2

// ================== LED STATE ==================
bool uploadLedActive = false;
unsigned long uploadLedUntilMs = 0;
const uint16_t UPLOAD_LED_HOLD_MS = 500;

uint8_t lastClientCount = 0;

// ================== WIFI MODE STATE ==================
enum WifiMode { MODE_AP, MODE_STA };
WifiMode currentMode = MODE_AP;
unsigned long lastScanMs = 0;

// ================== UPLOAD STATE ==================
File uploadFile;
String uploadFileName = "";
String uploadFilePath = "";
bool uploadOK = false;
String uploadError = "";
uint64_t uploadBytes = 0;

// ================== UPLOAD ALL STATE ==================
bool uploadAllActive = false;
uint32_t uploadAllOK = 0;
uint32_t uploadAllFailed = 0;
uint64_t uploadAllBytes = 0;
String uploadAllErrors = "";

// ================== UTILS ==================
String humanSize(uint64_t bytes)
{
  if (bytes < 1024)
    return String((unsigned long long)bytes) + " B";
  if (bytes < 1024ULL * 1024ULL)
    return String((double)bytes / 1024.0, 1) + " KB";
  return String((double)bytes / 1024.0 / 1024.0, 2) + " MB";
}

String jsonEscape(const String &s)
{
  String out = "";
  for (size_t i = 0; i < s.length(); i++)
  {
    char c = s[i];
    if (c == '"')
      out += "\\\"";
    else if (c == '\\')
      out += "\\\\";
    else if (c == '\n')
      out += "\\n";
    else if (c == '\r')
      out += "\\r";
    else
      out += c;
  }
  return out;
}

String safeFileName(String name)
{
  name.replace("\\", "/");

  int slash = name.lastIndexOf('/');
  if (slash >= 0)
    name = name.substring(slash + 1);

  String safe = "";
  for (size_t i = 0; i < name.length(); i++)
  {
    char c = name[i];
    if ((c >= 'a' && c <= 'z') ||
        (c >= 'A' && c <= 'Z') ||
        (c >= '0' && c <= '9') ||
        c == '.' || c == '_' || c == '-')
      safe += c;
    else
      safe += "_";
  }

  if (safe.length() == 0)
    safe = "upload.bin";

  if (safe.length() > 80)
    safe = safe.substring(safe.length() - 80);

  return safe;
}

bool initSDWithFallback()
{
  const uint32_t freqs[] = {20000000, 16000000, 10000000, 8000000, 4000000, 1000000, 400000};

  for (size_t i = 0; i < (sizeof(freqs) / sizeof(freqs[0])); i++)
  {
    uint32_t hz = freqs[i];
    SD.end();
    Serial.printf("[SD] Thu SD.begin o %lu Hz...\n", (unsigned long)hz);
    if (SD.begin(SD_CS, spiSD, hz, "/sd", 8, SD_FORMAT_IF_EMPTY))
    {
      mountedSDFreq = hz;
      Serial.printf("[SD] OK: Mount SD thanh cong o %lu Hz\n", (unsigned long)hz);
      return true;
    }
  }
  return false;
}

bool ensureUploadDir()
{
  if (!SD.exists("/uploads"))
    return SD.mkdir("/uploads");
  return true;
}

uint64_t getUsedBytes()  { return SD.usedBytes(); }
uint64_t getTotalBytes() { return SD.totalBytes(); }

// ================== LED FUNCTIONS ==================
void ledAllOff()
{
  digitalWrite(LED_RED, LOW);
  digitalWrite(LED_GREEN, LOW);
  digitalWrite(LED_WHITE, LOW);
}

void ledRed(bool on)   { digitalWrite(LED_RED,   on ? HIGH : LOW); }
void ledGreen(bool on) { digitalWrite(LED_GREEN, on ? HIGH : LOW); }
void ledWhite(bool on) { digitalWrite(LED_WHITE, on ? HIGH : LOW); }
void ledPink(bool on)  { digitalWrite(LED_RED, on ? HIGH : LOW); digitalWrite(LED_WHITE, on ? HIGH : LOW); }

void setWifiLedByClient()
{
  uint8_t clientCount = WiFi.softAPgetStationNum();
  ledGreen(clientCount > 0);
  lastClientCount = clientCount;
}

void startUploadBlinkWhite()
{
  unsigned long now = millis();
  uploadLedActive = true;
  if ((int32_t)(uploadLedUntilMs - now) <= 0)
    uploadLedUntilMs = now + UPLOAD_LED_HOLD_MS;
  else
    uploadLedUntilMs += UPLOAD_LED_HOLD_MS;
  ledWhite(true);
}

void updateStatusLed()
{
  unsigned long now = millis();

  if (uploadLedActive)
  {
    if ((int32_t)(uploadLedUntilMs - now) > 0)
    {
      ledWhite(true);
      return;
    }
    uploadLedActive = false;
    ledWhite(false);
    if (currentMode == MODE_STA)
    {
      ledPink(true);
    }
    else
    {
      setWifiLedByClient();
    }
    return;
  }

  if (currentMode == MODE_STA)
  {
    ledPink(true);
    return;
  }

  uint8_t clientCount = WiFi.softAPgetStationNum();
  if (clientCount != lastClientCount)
    setWifiLedByClient();
}

// ================== API HANDLERS ==================
void handleRoot()
{
  String ip = (currentMode == MODE_STA) ? WiFi.localIP().toString() : WiFi.softAPIP().toString();
  String msg = "ESP32-S3 microSD WiFi Server\n";
  msg += "Mode: ";
  msg += (currentMode == MODE_STA) ? "STA" : "AP";
  msg += "\nIP: " + ip + "\nPort: " + String(SERVER_PORT) + "\n\n";
  msg += "API:\n";
  msg += "GET    /api/status\n";
  msg += "GET    /api/filelist\n";
  msg += "POST   /api/upload     form-data key=file\n";
  msg += "POST   /api/upload-all form-data key=files\n";
  msg += "GET    /api/download?name=filename.bin\n";
  msg += "DELETE /api/delete?name=filename.bin\n";
  msg += "DELETE /api/delete-all\n";
  server.send(200, "text/plain", msg);
}

void handleStatus()
{
  uint8_t cardType = SD.cardType();
  String card = "UNKNOWN";
  if (cardType == CARD_NONE)       card = "NONE";
  else if (cardType == CARD_MMC)   card = "MMC";
  else if (cardType == CARD_SD)    card = "SDSC";
  else if (cardType == CARD_SDHC)  card = "SDHC/SDXC";

  String ip = (currentMode == MODE_STA) ? WiFi.localIP().toString() : WiFi.softAPIP().toString();

  String json = "{";
  json += "\"ok\":true,";
  json += "\"board\":\"ESP32-S3\",";
  json += "\"wifi_mode\":\"" + String(currentMode == MODE_STA ? "STA" : "AP") + "\",";
  json += "\"ip\":\"" + ip + "\",";
  json += "\"port\":" + String(SERVER_PORT) + ",";
  json += "\"ap_ssid\":\"" + jsonEscape(String(AP_SSID)) + "\",";
  json += "\"ap_pass\":\"" + jsonEscape(String(AP_PASS)) + "\",";
  json += "\"sd_card_type\":\"" + card + "\",";
  json += "\"sd_spi_hz\":" + String((unsigned long)mountedSDFreq) + ",";
  json += "\"sd_total\":" + String((unsigned long long)getTotalBytes()) + ",";
  json += "\"sd_used\":" + String((unsigned long long)getUsedBytes()) + ",";
  json += "\"sd_total_human\":\"" + humanSize(getTotalBytes()) + "\",";
  json += "\"sd_used_human\":\"" + humanSize(getUsedBytes()) + "\"";
  json += "}";

  server.send(200, "application/json", json);
}

void handleFileList()
{
  File dir = SD.open("/uploads");
  if (!dir || !dir.isDirectory())
  {
    server.send(500, "application/json", "{\"ok\":false,\"error\":\"cannot_open_uploads_dir\"}");
    return;
  }

  String json = "{\"ok\":true,\"dir\":\"/uploads\",\"files\":[";
  bool first = true;

  while (true)
  {
    File file = dir.openNextFile();
    if (!file) break;

    if (!file.isDirectory())
    {
      String fullName = String(file.name());
      int slash = fullName.lastIndexOf('/');
      String name = (slash >= 0) ? fullName.substring(slash + 1) : fullName;
      uint64_t size = file.size();

      if (!first) json += ",";
      first = false;

      json += "{";
      json += "\"name\":\"" + jsonEscape(name) + "\",";
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

String getNameArg()
{
  if (!server.hasArg("name")) return "";
  return safeFileName(server.arg("name"));
}

void handleDownload()
{
  String name = getNameArg();
  if (name.length() == 0)
  {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"missing_name\"}");
    return;
  }

  String path = "/uploads/" + name;
  if (!SD.exists(path))
  {
    server.send(404, "application/json", "{\"ok\":false,\"error\":\"file_not_found\"}");
    return;
  }

  File file = SD.open(path, FILE_READ);
  if (!file)
  {
    server.send(500, "application/json", "{\"ok\":false,\"error\":\"cannot_open_file\"}");
    return;
  }

  server.sendHeader("Content-Disposition", "attachment; filename=\"" + name + "\"");
  server.streamFile(file, "application/octet-stream");
  file.close();
}

void handleDelete()
{
  String name = getNameArg();
  if (name.length() == 0)
  {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"missing_name\"}");
    return;
  }

  String path = "/uploads/" + name;
  if (!SD.exists(path))
  {
    server.send(404, "application/json", "{\"ok\":false,\"error\":\"file_not_found\"}");
    return;
  }

  if (SD.remove(path))
    server.send(200, "application/json", "{\"ok\":true,\"deleted\":\"" + jsonEscape(name) + "\"}");
  else
    server.send(500, "application/json", "{\"ok\":false,\"error\":\"delete_failed\"}");
}

void handleDeleteAll()
{
  File dir = SD.open("/uploads");
  if (!dir || !dir.isDirectory())
  {
    server.send(500, "application/json", "{\"ok\":false,\"error\":\"cannot_open_uploads_dir\"}");
    return;
  }

  uint32_t deleted = 0, failed = 0;

  while (true)
  {
    File file = dir.openNextFile();
    if (!file) break;

    if (!file.isDirectory())
    {
      String fullName = String(file.name());
      int slash = fullName.lastIndexOf('/');
      String name = (slash >= 0) ? fullName.substring(slash + 1) : fullName;
      file.close();

      if (SD.remove("/uploads/" + name))
        deleted++;
      else
        failed++;
    }
    else
    {
      file.close();
    }
  }
  dir.close();

  String json = "{\"ok\":";
  json += (failed == 0 ? "true" : "false");
  json += ",\"deleted\":" + String(deleted) + ",\"failed\":" + String(failed) + "}";
  server.send(failed == 0 ? 200 : 500, "application/json", json);
}

void handleUploadResponse()
{
  if (uploadOK)
  {
    String json = "{\"ok\":true,";
    json += "\"name\":\"" + jsonEscape(uploadFileName) + "\",";
    json += "\"size\":" + String((unsigned long long)uploadBytes) + ",";
    json += "\"size_human\":\"" + humanSize(uploadBytes) + "\"}";
    server.send(200, "application/json", json);
  }
  else
  {
    server.send(500, "application/json",
      "{\"ok\":false,\"error\":\"" + jsonEscape(uploadError) + "\"}");
  }
}

void handleFileUpload()
{
  HTTPUpload &upload = server.upload();

  if (upload.status == UPLOAD_FILE_START)
  {
    uploadOK = false;
    uploadError = "";
    uploadBytes = 0;
    uploadFileName = safeFileName(upload.filename);
    uploadFilePath = "/uploads/" + uploadFileName;

    Serial.printf("[UPLOAD] START %s\n", uploadFileName.c_str());

    if (!ensureUploadDir()) { uploadError = "cannot_create_uploads_dir"; return; }
    if (SD.exists(uploadFilePath)) SD.remove(uploadFilePath);

    uploadFile = SD.open(uploadFilePath, FILE_WRITE);
    if (!uploadFile) { uploadError = "cannot_open_file_for_write"; return; }
  }
  else if (upload.status == UPLOAD_FILE_WRITE)
  {
    if (uploadFile)
    {
      size_t written = uploadFile.write(upload.buf, upload.currentSize);
      uploadBytes += written;
      if (written != upload.currentSize) uploadError = "sd_write_failed";
    }
    else
    {
      uploadError = "upload_file_not_open";
    }
  }
  else if (upload.status == UPLOAD_FILE_END)
  {
    if (uploadFile) { uploadFile.flush(); uploadFile.close(); }

    if (uploadError.length() == 0 && uploadBytes > 0)
    {
      uploadOK = true;
      startUploadBlinkWhite();
      Serial.printf("[UPLOAD] OK %s %llu bytes\n", uploadFileName.c_str(), (unsigned long long)uploadBytes);
    }
    else
    {
      if (uploadError.length() == 0) uploadError = "empty_upload";
      if (SD.exists(uploadFilePath)) SD.remove(uploadFilePath);
      Serial.printf("[UPLOAD] FAIL %s err=%s\n", uploadFileName.c_str(), uploadError.c_str());
    }
  }
  else if (upload.status == UPLOAD_FILE_ABORTED)
  {
    if (uploadFile) uploadFile.close();
    if (SD.exists(uploadFilePath)) SD.remove(uploadFilePath);
    uploadOK = false;
    uploadError = "upload_aborted";
  }
}

// ================== UPLOAD ALL HANDLERS ==================
void resetUploadAllState()
{
  uploadAllActive = true;
  uploadAllOK = 0;
  uploadAllFailed = 0;
  uploadAllBytes = 0;
  uploadAllErrors = "";
}

void handleUploadAllResponse()
{
  if (!uploadAllActive)
  {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"no_files_uploaded\"}");
    return;
  }

  String json = "{\"ok\":";
  json += (uploadAllFailed == 0 ? "true" : "false");
  json += ",\"uploaded\":" + String(uploadAllOK);
  json += ",\"failed\":" + String(uploadAllFailed);
  json += ",\"total_bytes\":" + String((unsigned long long)uploadAllBytes);
  json += ",\"total_human\":\"" + humanSize(uploadAllBytes) + "\"";
  if (uploadAllErrors.length() > 0)
    json += ",\"errors\":\"" + jsonEscape(uploadAllErrors) + "\"";
  json += "}";

  uploadAllActive = false;
  server.send(uploadAllFailed == 0 ? 200 : 500, "application/json", json);
}

void handleFileUploadAll()
{
  HTTPUpload &upload = server.upload();

  if (upload.status == UPLOAD_FILE_START)
  {
    if (!uploadAllActive) resetUploadAllState();

    uploadOK = false;
    uploadError = "";
    uploadBytes = 0;
    uploadFileName = safeFileName(upload.filename);
    uploadFilePath = "/uploads/" + uploadFileName;

    Serial.printf("[UPLOAD-ALL] START %s\n", uploadFileName.c_str());

    if (!ensureUploadDir())
    {
      uploadError = "cannot_create_uploads_dir";
      uploadAllFailed++;
      uploadAllErrors += uploadFileName + ": cannot_create_uploads_dir; ";
      return;
    }

    if (SD.exists(uploadFilePath)) SD.remove(uploadFilePath);

    uploadFile = SD.open(uploadFilePath, FILE_WRITE);
    if (!uploadFile)
    {
      uploadError = "cannot_open_file_for_write";
      uploadAllFailed++;
      uploadAllErrors += uploadFileName + ": cannot_open_file_for_write; ";
      return;
    }
  }
  else if (upload.status == UPLOAD_FILE_WRITE)
  {
    if (uploadFile)
    {
      size_t written = uploadFile.write(upload.buf, upload.currentSize);
      uploadBytes += written;
      if (written != upload.currentSize) uploadError = "sd_write_failed";
    }
    else
    {
      uploadError = "upload_file_not_open";
    }
  }
  else if (upload.status == UPLOAD_FILE_END)
  {
    if (uploadFile) { uploadFile.flush(); uploadFile.close(); }

    if (uploadError.length() == 0 && uploadBytes > 0)
    {
      uploadOK = true;
      uploadAllOK++;
      uploadAllBytes += uploadBytes;
      startUploadBlinkWhite();
      Serial.printf("[UPLOAD-ALL] OK %s %llu bytes\n", uploadFileName.c_str(), (unsigned long long)uploadBytes);
    }
    else
    {
      if (uploadError.length() == 0) uploadError = "empty_upload";
      uploadAllFailed++;
      uploadAllErrors += uploadFileName + ": " + uploadError + "; ";
      if (SD.exists(uploadFilePath)) SD.remove(uploadFilePath);
      Serial.printf("[UPLOAD-ALL] FAIL %s err=%s\n", uploadFileName.c_str(), uploadError.c_str());
    }
  }
  else if (upload.status == UPLOAD_FILE_ABORTED)
  {
    if (uploadFile) uploadFile.close();
    if (SD.exists(uploadFilePath)) SD.remove(uploadFilePath);
    uploadOK = false;
    uploadError = "upload_aborted";
    uploadAllFailed++;
    uploadAllErrors += uploadFileName + ": upload_aborted; ";
  }
}

// ================== WIFI SWITCHING ==================
void notifyTrustedHost()
{
  String ip = WiFi.localIP().toString();
  String gw = WiFi.gatewayIP().toString();
  String payload = "{\"ip\":\"" + ip + "\",\"type\":\"B\"}";
  String url = "http://" + gw + ":" + String(SERVER_PORT) + "/api/device/connection";

  HTTPClient http;
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(payload);
  Serial.printf("[NOTIFY] POST %s -> %d\n", url.c_str(), code);
  http.end();
}
void startAP()
{
  server.stop();
  WiFi.softAPdisconnect(true);
  WiFi.disconnect(true);
  delay(300);
  ledAllOff();

  WiFi.mode(WIFI_AP);
  WiFi.setSleep(false);
  WiFi.setTxPower(WIFI_POWER_19_5dBm);
  WiFi.softAPConfig(local_IP, gateway, subnet);
  WiFi.softAP(AP_SSID, AP_PASS, AP_CHANNEL, AP_HIDDEN, AP_MAX_CONN);

  currentMode = MODE_AP;
  lastClientCount = 0;
  Serial.printf("[WIFI] AP: %s  IP: %s\n", AP_SSID, WiFi.softAPIP().toString().c_str());
  server.begin();
}

bool connectToTrusted()
{
  server.stop();
  WiFi.softAPdisconnect(true);
  WiFi.disconnect(true);
  delay(300);

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(TRUSTED_SSID, TRUSTED_PASS);

  Serial.printf("[WIFI] Dang ket noi %s...\n", TRUSTED_SSID);
  unsigned long t = millis();
  while (WiFi.status() != WL_CONNECTED)
  {
    if (millis() - t > STA_CONNECT_TIMEOUT_MS)
    {
      Serial.println("[WIFI] Ket noi that bai, quay ve AP.");
      startAP();
      return false;
    }
    delay(200);
  }

  currentMode = MODE_STA;
  Serial.printf("[WIFI] STA connected. IP: %s\n", WiFi.localIP().toString().c_str());
  notifyTrustedHost();
  server.begin();
  return true;
}

void doWifiScan()
{
  Serial.println("[SCAN] Quet WiFi tim trusted AP...");
  int n = WiFi.scanNetworks(false, true);
  bool found = false;

  for (int i = 0; i < n; i++)
  {
    if (WiFi.SSID(i) == TRUSTED_SSID)
    {
      found = true;
      break;
    }
  }
  WiFi.scanDelete();

  if (found)
  {
    Serial.println("[SCAN] Tim thay trusted AP, chuyen sang STA.");
    connectToTrusted();
  }
  else
  {
    Serial.println("[SCAN] Khong tim thay, giu AP.");
  }
}

void checkSTAConnection()
{
  if (WiFi.status() != WL_CONNECTED)
  {
    Serial.println("[WIFI] Mat ket noi STA, quay ve AP.");
    startAP();
  }
}

void handleNotFound()
{
  String json = "{\"ok\":false,\"error\":\"not_found\",\"uri\":\"" + jsonEscape(server.uri()) + "\"}";
  server.send(404, "application/json", json);
}

// ================== SETUP ==================
void setup()
{
  Serial.begin(115200);
  delay(1500);

  Serial.println();
  Serial.println("=== ESP32-S3 microSD WIFI SERVER ===");

  pinMode(LED_RED, OUTPUT);
  pinMode(LED_GREEN, OUTPUT);
  pinMode(LED_WHITE, OUTPUT);
  ledAllOff();
  ledRed(true);

  Serial.println("[SD] Khoi tao SPI...");
  spiSD.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);

  Serial.println("[SD] Dang khoi tao the nho...");
  if (!initSDWithFallback())
  {
    Serial.println("[SD] FAIL: Khong nhan duoc the nho!");
    while (true) { ledRed(true); delay(300); ledRed(false); delay(300); }
  }

  if (!ensureUploadDir())
  {
    Serial.println("[SD] FAIL: Khong tao duoc /uploads");
    while (true) { ledRed(true); delay(100); ledRed(false); delay(100); }
  }

  uint8_t cardType = SD.cardType();
  Serial.print("[SD] Loai the: ");
  if (cardType == CARD_MMC)        Serial.println("MMC");
  else if (cardType == CARD_SD)    Serial.println("SDSC");
  else if (cardType == CARD_SDHC)  Serial.println("SDHC/SDXC");
  else                             Serial.println("UNKNOWN");

  Serial.printf("[SD] Total: %s  Used: %s\n",
    humanSize(SD.totalBytes()).c_str(), humanSize(SD.usedBytes()).c_str());

  Serial.println("[WIFI] Quet WiFi tim trusted AP...");
  WiFi.mode(WIFI_STA);
  int n = WiFi.scanNetworks(false, true);
  bool trustedFound = false;
  for (int i = 0; i < n; i++)
  {
    if (WiFi.SSID(i) == TRUSTED_SSID)
    {
      trustedFound = true;
      break;
    }
  }
  WiFi.scanDelete();

  if (trustedFound)
  {
    Serial.println("[WIFI] Tim thay trusted AP, ket noi STA...");
    WiFi.setSleep(false);
    WiFi.begin(TRUSTED_SSID, TRUSTED_PASS);
    unsigned long t = millis();
    bool connected = false;
    while (millis() - t < STA_CONNECT_TIMEOUT_MS)
    {
      if (WiFi.status() == WL_CONNECTED) { connected = true; break; }
      delay(200);
    }
    if (connected)
    {
      currentMode = MODE_STA;
      ledRed(false);
      Serial.printf("[WIFI] STA connected. IP: %s\n", WiFi.localIP().toString().c_str());
      notifyTrustedHost();
    }
    else
    {
      Serial.println("[WIFI] Ket noi STA that bai, chuyen sang AP.");
    }
  }

  if (currentMode != MODE_STA)
  {
    Serial.println("[WIFI] Cau hinh IP AP...");
    WiFi.mode(WIFI_AP);
    WiFi.setSleep(false);
    WiFi.setTxPower(WIFI_POWER_19_5dBm);
    WiFi.softAPConfig(local_IP, gateway, subnet);
    if (!WiFi.softAP(AP_SSID, AP_PASS, AP_CHANNEL, AP_HIDDEN, AP_MAX_CONN))
    {
      Serial.println("[WIFI] FAIL: Khong phat duoc WiFi AP");
      while (true) { ledRed(true); delay(500); ledRed(false); delay(500); }
    }
    ledRed(false);
    Serial.printf("[WIFI] SSID: %s  IP: %s  PORT: %d\n",
      AP_SSID, WiFi.softAPIP().toString().c_str(), SERVER_PORT);
  }

  setWifiLedByClient();

  server.on("/", HTTP_GET, handleRoot);
  server.on("/api/status", HTTP_GET, handleStatus);
  server.on("/api/filelist", HTTP_GET, handleFileList);
  server.on("/api/download", HTTP_GET, handleDownload);

  server.on("/api/delete", HTTP_GET,    handleDelete);
  server.on("/api/delete", HTTP_POST,   handleDelete);
  server.on("/api/delete", HTTP_DELETE, handleDelete);

  server.on("/api/delete-all", HTTP_POST,   handleDeleteAll);
  server.on("/api/delete-all", HTTP_DELETE, handleDeleteAll);

  server.on("/api/upload",     HTTP_POST, handleUploadResponse, handleFileUpload);
  server.on("/api/upload-all", HTTP_POST, handleUploadAllResponse, handleFileUploadAll);

  server.onNotFound(handleNotFound);

  server.begin();
  lastScanMs = millis();

  Serial.println("[HTTP] Server started");
  Serial.println("=== READY ===");
}

// ================== LOOP ==================
void loop()
{
  server.handleClient();
  updateStatusLed();

  unsigned long now = millis();

  if (currentMode == MODE_STA)
  {
    checkSTAConnection();
  }
  else if (now - lastScanMs >= SCAN_INTERVAL_MS)
  {
    lastScanMs = now;
    doWifiScan();
  }
}
