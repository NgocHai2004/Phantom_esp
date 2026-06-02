#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <SPI.h>
#include <SD.h>
#include <FS.h>
#include <driver/i2s_std.h>

// ================== microSD PIN - ESP32-S3 ==================
#define SD_CS 10
#define SD_SCK 12
#define SD_MOSI 11
#define SD_MISO 13

#define SD_FORMAT_IF_EMPTY false

SPIClass spiSD(FSPI);

uint32_t mountedSDFreq = 0;

// ================== I2S MIC PIN - ICS-43434 ==================
#define I2S_BCLK 4
#define I2S_WS 5
#define I2S_SD 6

#define REC_SAMPLE_RATE 16000
#define REC_BITS 16
#define REC_CHANNELS 1
#define REC_DURATION_SEC 30
#define REC_DMA_BUF_COUNT 4
#define REC_DMA_BUF_LEN 256

i2s_chan_handle_t i2s_rx_handle = NULL;
bool recActive = false;
uint32_t recFileIndex = 0;

static int32_t i2sBuf[REC_DMA_BUF_LEN];
static int16_t pcmBuf[REC_DMA_BUF_LEN];

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

// ================== LED STATE ==================
bool uploadLedActive = false;
unsigned long uploadLedUntilMs = 0;
const uint16_t UPLOAD_LED_HOLD_MS = 500;

uint8_t lastClientCount = 0;

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
  {
    name = name.substring(slash + 1);
  }

  String safe = "";
  for (size_t i = 0; i < name.length(); i++)
  {
    char c = name[i];

    if ((c >= 'a' && c <= 'z') ||
        (c >= 'A' && c <= 'Z') ||
        (c >= '0' && c <= '9') ||
        c == '.' || c == '_' || c == '-')
    {
      safe += c;
    }
    else
    {
      safe += "_";
    }
  }

  if (safe.length() == 0)
  {
    safe = "upload.bin";
  }

  if (safe.length() > 80)
  {
    safe = safe.substring(safe.length() - 80);
  }

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
  {
    return SD.mkdir("/uploads");
  }
  return true;
}

uint64_t getUsedBytes()
{
  return SD.usedBytes();
}

uint64_t getTotalBytes()
{
  return SD.totalBytes();
}

// ================== LED FUNCTIONS ==================
void ledAllOff()
{
  digitalWrite(LED_RED, LOW);
  digitalWrite(LED_GREEN, LOW);
  digitalWrite(LED_WHITE, LOW);
}

void ledRed(bool on)
{
  digitalWrite(LED_RED, on ? HIGH : LOW);
}

void ledGreen(bool on)
{
  digitalWrite(LED_GREEN, on ? HIGH : LOW);
}

void ledWhite(bool on)
{
  digitalWrite(LED_WHITE, on ? HIGH : LOW);
}

void setWifiLedByClient()
{
  uint8_t clientCount = WiFi.softAPgetStationNum();

  if (clientCount > 0)
  {
    ledGreen(true);
  }
  else
  {
    ledGreen(false);
  }

  lastClientCount = clientCount;
}

void startUploadBlinkWhite()
{
  unsigned long now = millis();

  uploadLedActive = true;

  if ((int32_t)(uploadLedUntilMs - now) <= 0)
  {
    uploadLedUntilMs = now + UPLOAD_LED_HOLD_MS;
  }
  else
  {
    uploadLedUntilMs += UPLOAD_LED_HOLD_MS;
  }

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
    setWifiLedByClient();
    return;
  }

  uint8_t clientCount = WiFi.softAPgetStationNum();
  if (clientCount != lastClientCount)
  {
    setWifiLedByClient();
  }
}

// ================== I2S RECORDING ==================
bool initI2SMic()
{
  i2s_chan_config_t chan_cfg = {
      .id = I2S_NUM_0,
      .role = I2S_ROLE_MASTER,
      .dma_desc_num = REC_DMA_BUF_COUNT,
      .dma_frame_num = REC_DMA_BUF_LEN,
      .auto_clear_after_cb = true,
      .auto_clear_before_cb = false,
      .intr_priority = 0,
  };

  if (i2s_new_channel(&chan_cfg, NULL, &i2s_rx_handle) != ESP_OK)
  {
    Serial.println("[MIC] FAIL: i2s_new_channel");
    return false;
  }

  i2s_std_config_t std_cfg = {
      .clk_cfg = {
          .sample_rate_hz = REC_SAMPLE_RATE,
          .clk_src = I2S_CLK_SRC_DEFAULT,
          .ext_clk_freq_hz = 0,
          .mclk_multiple = I2S_MCLK_MULTIPLE_256,
      },
      .slot_cfg = {
          .data_bit_width = I2S_DATA_BIT_WIDTH_32BIT,
          .slot_bit_width = I2S_SLOT_BIT_WIDTH_AUTO,
          .slot_mode = I2S_SLOT_MODE_MONO,
          .slot_mask = I2S_STD_SLOT_LEFT,
          .ws_width = I2S_DATA_BIT_WIDTH_32BIT,
          .ws_pol = false,
          .bit_shift = true,
          .left_align = true,
          .big_endian = false,
          .bit_order_lsb = false,
      },
      .gpio_cfg = {
          .mclk = I2S_GPIO_UNUSED,
          .bclk = (gpio_num_t)I2S_BCLK,
          .ws = (gpio_num_t)I2S_WS,
          .dout = I2S_GPIO_UNUSED,
          .din = (gpio_num_t)I2S_SD,
          .invert_flags = {
              .mclk_inv = false,
              .bclk_inv = false,
              .ws_inv = false,
          },
      },
  };

  if (i2s_channel_init_std_mode(i2s_rx_handle, &std_cfg) != ESP_OK)
  {
    Serial.println("[MIC] FAIL: i2s_channel_init_std_mode");
    i2s_del_channel(i2s_rx_handle);
    i2s_rx_handle = NULL;
    return false;
  }

  Serial.println("[MIC] I2S init OK (ICS-43434, 16kHz mono)");
  return true;
}

void writeWavHeader(File &f, uint32_t dataSize)
{
  uint16_t bitsPerSample = REC_BITS;
  uint16_t numChannels = REC_CHANNELS;
  uint32_t sampleRate = REC_SAMPLE_RATE;
  uint16_t blockAlign = numChannels * (bitsPerSample / 8);
  uint32_t byteRate = sampleRate * blockAlign;
  uint32_t chunkSize = 36 + dataSize;

  f.write((const uint8_t *)"RIFF", 4);
  f.write((const uint8_t *)&chunkSize, 4);
  f.write((const uint8_t *)"WAVE", 4);
  f.write((const uint8_t *)"fmt ", 4);
  uint32_t subchunk1Size = 16;
  f.write((const uint8_t *)&subchunk1Size, 4);
  uint16_t audioFormat = 1; // PCM
  f.write((const uint8_t *)&audioFormat, 2);
  f.write((const uint8_t *)&numChannels, 2);
  f.write((const uint8_t *)&sampleRate, 4);
  f.write((const uint8_t *)&byteRate, 4);
  f.write((const uint8_t *)&blockAlign, 2);
  f.write((const uint8_t *)&bitsPerSample, 2);
  f.write((const uint8_t *)"data", 4);
  f.write((const uint8_t *)&dataSize, 4);
}

void doRecording()
{
  if (recActive)
  {
    Serial.println("[REC] Dang ghi am, gui 'stop' de dung.");
    return;
  }

  if (!i2s_rx_handle)
  {
    if (!initI2SMic())
    {
      Serial.println("[REC] Khong khoi tao duoc I2S mic!");
      return;
    }
  }

  recFileIndex++;
  String fileName = "/uploads/rec_" + String(recFileIndex) + ".wav";

  if (!ensureUploadDir())
  {
    Serial.println("[REC] Khong tao duoc /uploads");
    return;
  }

  File wavFile = SD.open(fileName, FILE_WRITE);
  if (!wavFile)
  {
    Serial.println("[REC] Khong mo duoc file: " + fileName);
    return;
  }

  uint32_t totalSamples = REC_SAMPLE_RATE * REC_DURATION_SEC;
  uint32_t expectedDataSize = totalSamples * REC_CHANNELS * (REC_BITS / 8);

  writeWavHeader(wavFile, expectedDataSize);

  if (i2s_channel_enable(i2s_rx_handle) != ESP_OK)
  {
    Serial.println("[REC] FAIL: i2s_channel_enable");
    wavFile.close();
    SD.remove(fileName);
    return;
  }

  recActive = true;
  Serial.println("[REC] === BAT DAU GHI AM ===");
  Serial.printf("[REC] File: %s\n", fileName.c_str());
  Serial.printf("[REC] %d Hz, %d-bit, mono, %d giay\n", REC_SAMPLE_RATE, REC_BITS, REC_DURATION_SEC);
  Serial.println("[REC] Gui 'stop' de dung som.");

  ledAllOff();

  uint32_t samplesWritten = 0;
  size_t bytesRead = 0;
  unsigned long startMs = millis();
  unsigned long lastProgressMs = startMs;
  bool stopped = false;

  while (samplesWritten < totalSamples)
  {
    if (((millis() - startMs) / 500) % 2 == 0)
      ledWhite(true);
    else
      ledWhite(false);

    if (i2s_channel_read(i2s_rx_handle, i2sBuf, sizeof(i2sBuf), &bytesRead, 100) != ESP_OK)
    {
      continue;
    }

    size_t samplesRead = bytesRead / sizeof(int32_t);

    for (size_t i = 0; i < samplesRead; i++)
    {
      pcmBuf[i] = (int16_t)(i2sBuf[i] >> 14);
    }

    uint32_t samplesToWrite = samplesRead;
    if (samplesWritten + samplesToWrite > totalSamples)
    {
      samplesToWrite = totalSamples - samplesWritten;
    }

    wavFile.write((const uint8_t *)pcmBuf, samplesToWrite * sizeof(int16_t));
    samplesWritten += samplesToWrite;

    if (millis() - lastProgressMs >= 5000)
    {
      wavFile.flush();
      uint32_t elapsed = (millis() - startMs) / 1000;
      Serial.printf("[REC] %lu/%d giay (%lu samples)\n", (unsigned long)elapsed, REC_DURATION_SEC, (unsigned long)samplesWritten);
      lastProgressMs = millis();
    }

    if (Serial.available())
    {
      String cmd = Serial.readStringUntil('\n');
      cmd.trim();
      if (cmd == "stop")
      {
        stopped = true;
        break;
      }
    }
  }

  i2s_channel_disable(i2s_rx_handle);
  recActive = false;

  uint32_t actualDataSize = samplesWritten * REC_CHANNELS * (REC_BITS / 8);
  wavFile.seek(0);
  writeWavHeader(wavFile, actualDataSize);
  wavFile.close();

  ledWhite(false);
  ledGreen(true);
  delay(1000);
  ledGreen(false);
  setWifiLedByClient();

  float duration = (float)samplesWritten / REC_SAMPLE_RATE;
  Serial.println("[REC] === HOAN THANH ===");
  Serial.printf("[REC] File: %s\n", fileName.c_str());
  Serial.printf("[REC] Thoi luong: %.1f giay\n", duration);
  Serial.printf("[REC] Kich thuoc: %s\n", humanSize(actualDataSize + 44).c_str());
  if (stopped)
    Serial.println("[REC] (Dung som boi lenh 'stop')");
}

void handleSerialCommand()
{
  if (!Serial.available())
    return;

  String cmd = Serial.readStringUntil('\n');
  cmd.trim();

  if (cmd == "rec" || cmd == "record")
  {
    doRecording();
  }
  else if (cmd == "help")
  {
    Serial.println("=== SERIAL COMMANDS ===");
    Serial.println("rec    - Ghi am 30 giay tu mic ICS-43434 vao SD");
    Serial.println("stop   - Dung ghi am som");
    Serial.println("help   - Hien thi menu nay");
  }
}

// ================== API HANDLERS ==================
void handleRoot()
{
  String msg = "";
  msg += "ESP32-S3 microSD Upload Server\n";
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
  msg += "GET    /api/status\n";
  msg += "GET    /api/filelist\n";
  msg += "POST   /api/upload     form-data key=file\n";
  msg += "POST   /api/upload-all form-data key=files, support multi files\n";
  msg += "GET    /api/download?name=filename.bin\n";
  msg += "GET    /api/delete?name=filename.bin\n";
  msg += "POST   /api/delete?name=filename.bin\n";
  msg += "DELETE /api/delete?name=filename.bin\n";
  msg += "POST   /api/delete-all\n";
  msg += "DELETE /api/delete-all\n";

  server.send(200, "text/plain", msg);
}

void handleStatus()
{
  uint8_t cardType = SD.cardType();

  String card = "UNKNOWN";
  if (cardType == CARD_NONE)
    card = "NONE";
  else if (cardType == CARD_MMC)
    card = "MMC";
  else if (cardType == CARD_SD)
    card = "SDSC";
  else if (cardType == CARD_SDHC)
    card = "SDHC/SDXC";

  String json = "{";
  json += "\"ok\":true,";
  json += "\"board\":\"ESP32-S3\",";
  json += "\"wifi_mode\":\"AP\",";
  json += "\"ssid\":\"" + jsonEscape(String(AP_SSID)) + "\",";
  json += "\"ip\":\"" + WiFi.softAPIP().toString() + "\",";
  json += "\"port\":" + String(SERVER_PORT) + ",";
  json += "\"sd_card_type\":\"" + card + "\",";
  json += "\"sd_spi_hz\":" + String((unsigned long)mountedSDFreq) + ",";
  json += "\"sd_spi_mhz\":\"" + String((double)mountedSDFreq / 1000000.0, 1) + "\",";
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

  String json = "{";
  json += "\"ok\":true,";
  json += "\"dir\":\"/uploads\",";
  json += "\"files\":[";

  bool first = true;

  while (true)
  {
    File file = dir.openNextFile();
    if (!file)
      break;

    if (!file.isDirectory())
    {
      String fullName = String(file.name());

      int slash = fullName.lastIndexOf('/');
      String name = fullName;
      if (slash >= 0)
      {
        name = fullName.substring(slash + 1);
      }

      uint64_t size = file.size();

      if (!first)
        json += ",";
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

String getNameArg()
{
  if (!server.hasArg("name"))
    return "";
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

  bool ok = SD.remove(path);

  if (ok)
  {
    String json = "{\"ok\":true,\"deleted\":\"" + jsonEscape(name) + "\"}";
    server.send(200, "application/json", json);
  }
  else
  {
    server.send(500, "application/json", "{\"ok\":false,\"error\":\"delete_failed\"}");
  }
}

void handleDeleteAll()
{
  File dir = SD.open("/uploads");

  if (!dir || !dir.isDirectory())
  {
    server.send(500, "application/json", "{\"ok\":false,\"error\":\"cannot_open_uploads_dir\"}");
    return;
  }

  uint32_t deleted = 0;
  uint32_t failed = 0;

  while (true)
  {
    File file = dir.openNextFile();
    if (!file)
      break;

    if (!file.isDirectory())
    {
      String fullName = String(file.name());
      String name = fullName;

      int slash = fullName.lastIndexOf('/');
      if (slash >= 0)
      {
        name = fullName.substring(slash + 1);
      }

      file.close();

      String path = "/uploads/" + name;
      if (SD.remove(path))
      {
        deleted++;
      }
      else
      {
        failed++;
      }
    }
    else
    {
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

void handleUploadResponse()
{
  if (uploadOK)
  {
    String json = "{";
    json += "\"ok\":true,";
    json += "\"name\":\"" + jsonEscape(uploadFileName) + "\",";
    json += "\"path\":\"" + jsonEscape(uploadFilePath) + "\",";
    json += "\"size\":" + String((unsigned long long)uploadBytes) + ",";
    json += "\"size_human\":\"" + humanSize(uploadBytes) + "\"";
    json += "}";

    server.send(200, "application/json", json);
  }
  else
  {
    String json = "{";
    json += "\"ok\":false,";
    json += "\"error\":\"" + jsonEscape(uploadError) + "\"";
    json += "}";

    server.send(500, "application/json", json);
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

    Serial.printf("[UPLOAD] START name=%s path=%s\n", uploadFileName.c_str(), uploadFilePath.c_str());

    if (!ensureUploadDir())
    {
      uploadError = "cannot_create_uploads_dir";
      Serial.println("[UPLOAD] FAIL cannot create /uploads");
      return;
    }

    if (SD.exists(uploadFilePath))
    {
      SD.remove(uploadFilePath);
    }

    uploadFile = SD.open(uploadFilePath, FILE_WRITE);
    if (!uploadFile)
    {
      uploadError = "cannot_open_file_for_write";
      Serial.println("[UPLOAD] FAIL open file write");
      return;
    }
  }

  else if (upload.status == UPLOAD_FILE_WRITE)
  {
    if (uploadFile)
    {
      size_t written = uploadFile.write(upload.buf, upload.currentSize);
      uploadBytes += written;

      if (written != upload.currentSize)
      {
        uploadError = "sd_write_failed";
        Serial.printf("[UPLOAD] WRITE FAIL written=%u expected=%u\n",
                      (unsigned)written,
                      (unsigned)upload.currentSize);
      }
    }
    else
    {
      uploadError = "upload_file_not_open";
    }
  }

  else if (upload.status == UPLOAD_FILE_END)
  {
    if (uploadFile)
    {
      uploadFile.flush();
      uploadFile.close();
    }

    if (uploadError.length() == 0 && uploadBytes > 0)
    {
      uploadOK = true;
      startUploadBlinkWhite();

      Serial.printf("[UPLOAD] OK name=%s size=%llu\n",
                    uploadFileName.c_str(),
                    (unsigned long long)uploadBytes);
    }
    else
    {
      uploadOK = false;

      if (uploadError.length() == 0)
      {
        uploadError = "empty_upload";
      }

      if (uploadFilePath.length() > 0 && SD.exists(uploadFilePath))
      {
        SD.remove(uploadFilePath);
      }

      Serial.printf("[UPLOAD] FAIL name=%s err=%s\n",
                    uploadFileName.c_str(),
                    uploadError.c_str());
    }
  }

  else if (upload.status == UPLOAD_FILE_ABORTED)
  {
    if (uploadFile)
    {
      uploadFile.close();
    }

    if (uploadFilePath.length() > 0 && SD.exists(uploadFilePath))
    {
      SD.remove(uploadFilePath);
    }

    uploadOK = false;
    uploadError = "upload_aborted";

    Serial.printf("[UPLOAD] ABORTED name=%s\n", uploadFileName.c_str());
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

  String json = "{";
  json += "\"ok\":";
  json += (uploadAllFailed == 0 ? "true" : "false");
  json += ",";
  json += "\"uploaded\":";
  json += String(uploadAllOK);
  json += ",";
  json += "\"failed\":";
  json += String(uploadAllFailed);
  json += ",";
  json += "\"total_bytes\":";
  json += String((unsigned long long)uploadAllBytes);
  json += ",";
  json += "\"total_human\":\"";
  json += humanSize(uploadAllBytes);
  json += "\"";

  if (uploadAllErrors.length() > 0)
  {
    json += ",\"errors\":\"";
    json += jsonEscape(uploadAllErrors);
    json += "\"";
  }

  json += "}";

  uploadAllActive = false;

  server.send(uploadAllFailed == 0 ? 200 : 500, "application/json", json);
}

void handleFileUploadAll()
{
  HTTPUpload &upload = server.upload();

  if (upload.status == UPLOAD_FILE_START)
  {
    if (!uploadAllActive)
    {
      resetUploadAllState();
    }

    uploadOK = false;
    uploadError = "";
    uploadBytes = 0;

    uploadFileName = safeFileName(upload.filename);
    uploadFilePath = "/uploads/" + uploadFileName;

    Serial.printf("[UPLOAD-ALL] START name=%s path=%s\n",
                  uploadFileName.c_str(),
                  uploadFilePath.c_str());

    if (!ensureUploadDir())
    {
      uploadError = "cannot_create_uploads_dir";
      uploadAllFailed++;
      uploadAllErrors += uploadFileName + ": cannot_create_uploads_dir; ";
      Serial.println("[UPLOAD-ALL] FAIL cannot create /uploads");
      return;
    }

    if (SD.exists(uploadFilePath))
    {
      SD.remove(uploadFilePath);
    }

    uploadFile = SD.open(uploadFilePath, FILE_WRITE);
    if (!uploadFile)
    {
      uploadError = "cannot_open_file_for_write";
      uploadAllFailed++;
      uploadAllErrors += uploadFileName + ": cannot_open_file_for_write; ";
      Serial.println("[UPLOAD-ALL] FAIL open file write");
      return;
    }
  }

  else if (upload.status == UPLOAD_FILE_WRITE)
  {
    if (uploadFile)
    {
      size_t written = uploadFile.write(upload.buf, upload.currentSize);
      uploadBytes += written;

      if (written != upload.currentSize)
      {
        uploadError = "sd_write_failed";
        Serial.printf("[UPLOAD-ALL] WRITE FAIL written=%u expected=%u\n",
                      (unsigned)written,
                      (unsigned)upload.currentSize);
      }
    }
    else
    {
      uploadError = "upload_file_not_open";
    }
  }

  else if (upload.status == UPLOAD_FILE_END)
  {
    if (uploadFile)
    {
      uploadFile.flush();
      uploadFile.close();
    }

    if (uploadError.length() == 0 && uploadBytes > 0)
    {
      uploadOK = true;
      uploadAllOK++;
      uploadAllBytes += uploadBytes;

      startUploadBlinkWhite();

      Serial.printf("[UPLOAD-ALL] OK name=%s size=%llu\n",
                    uploadFileName.c_str(),
                    (unsigned long long)uploadBytes);
    }
    else
    {
      uploadOK = false;

      if (uploadError.length() == 0)
      {
        uploadError = "empty_upload";
      }

      uploadAllFailed++;
      uploadAllErrors += uploadFileName + ": " + uploadError + "; ";

      if (uploadFilePath.length() > 0 && SD.exists(uploadFilePath))
      {
        SD.remove(uploadFilePath);
      }

      Serial.printf("[UPLOAD-ALL] FAIL name=%s err=%s\n",
                    uploadFileName.c_str(),
                    uploadError.c_str());
    }
  }

  else if (upload.status == UPLOAD_FILE_ABORTED)
  {
    if (uploadFile)
    {
      uploadFile.close();
    }

    if (uploadFilePath.length() > 0 && SD.exists(uploadFilePath))
    {
      SD.remove(uploadFilePath);
    }

    uploadOK = false;
    uploadError = "upload_aborted";

    uploadAllFailed++;
    uploadAllErrors += uploadFileName + ": upload_aborted; ";

    Serial.printf("[UPLOAD-ALL] ABORTED name=%s\n", uploadFileName.c_str());
  }
}

void handleNotFound()
{
  String json = "{";
  json += "\"ok\":false,";
  json += "\"error\":\"not_found\",";
  json += "\"uri\":\"" + jsonEscape(server.uri()) + "\"";
  json += "}";

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
    Serial.println("Kiem tra day:");
    Serial.println("VCC  -> 3V3");
    Serial.println("GND  -> GND");
    Serial.println("SCK  -> GPIO12");
    Serial.println("MOSI -> GPIO11");
    Serial.println("MISO -> GPIO13");
    Serial.println("CS   -> GPIO10");
    while (true)
    {
      ledRed(true);
      delay(300);
      ledRed(false);
      delay(300);
    }
  }

  if (!ensureUploadDir())
  {
    Serial.println("[SD] FAIL: Khong tao duoc /uploads");
    while (true)
    {
      ledRed(true);
      delay(100);
      ledRed(false);
      delay(100);
    }
  }

  Serial.println("[SD] OK");

  uint8_t cardType = SD.cardType();
  Serial.print("[SD] Loai the: ");
  if (cardType == CARD_MMC)
    Serial.println("MMC");
  else if (cardType == CARD_SD)
    Serial.println("SDSC");
  else if (cardType == CARD_SDHC)
    Serial.println("SDHC/SDXC");
  else
    Serial.println("UNKNOWN");

  Serial.printf("[SD] Total: %s\n", humanSize(SD.totalBytes()).c_str());
  Serial.printf("[SD] Used : %s\n", humanSize(SD.usedBytes()).c_str());

  Serial.println("[WIFI] Cau hinh IP AP...");
  WiFi.mode(WIFI_AP);
  WiFi.setSleep(false);
  WiFi.setTxPower(WIFI_POWER_19_5dBm);
  WiFi.softAPConfig(local_IP, gateway, subnet);

  bool hidden = false;
  int channel = 1;
  int max_connection = 2;

  bool apOK = WiFi.softAP(AP_SSID, AP_PASS, channel, hidden, max_connection);

  if (!apOK)
  {
    Serial.println("[WIFI] FAIL: Khong phat duoc WiFi AP");
    while (true)
    {
      ledRed(true);
      delay(500);
      ledRed(false);
      delay(500);
    }
  }

  ledRed(false);

  Serial.println("[WIFI] OK: Da phat WiFi AP");
  Serial.printf("[WIFI] SSID: %s\n", AP_SSID);
  Serial.printf("[WIFI] PASS: %s\n", AP_PASS);
  Serial.printf("[WIFI] IP  : %s\n", WiFi.softAPIP().toString().c_str());
  Serial.printf("[WIFI] CH  : %d\n", channel);
  Serial.printf("[WIFI] HIDDEN: %s\n", hidden ? "YES" : "NO");
  Serial.printf("[WIFI] MAX CLIENT: %d\n", max_connection);
  Serial.printf("[HTTP] PORT: %d\n", SERVER_PORT);
  Serial.printf("[LED] RED: GPIO%d | GREEN: GPIO%d | WHITE: GPIO%d\n", LED_RED, LED_GREEN, LED_WHITE);

  setWifiLedByClient();

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
      handleFileUpload);

  server.on(
      "/api/upload-all",
      HTTP_POST,
      handleUploadAllResponse,
      handleFileUploadAll);

  server.onNotFound(handleNotFound);

  server.begin();

  Serial.println("[HTTP] Server started");
  Serial.println("=== READY ===");
  Serial.println("[REC] Tu dong ghi am 30 giay...");
  doRecording();
}

// ================== LOOP ==================
void loop()
{
  server.handleClient();
  updateStatusLed();
  handleSerialCommand();
}
