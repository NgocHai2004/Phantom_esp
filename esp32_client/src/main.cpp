#include <Arduino.h>
#include <WiFi.h>
#include <SD.h>
#include <SPI.h>
#include <FS.h>
#include <driver/i2s.h>
#include <mbedtls/gcm.h>
#include <esp_system.h>

// Minimal Phantom-2: MIC record -> AES-GCM .bin -> save SD -> upload when target SSID is available.

#define TARGET_SSID "9f86d081884c7d659a2f"
#define TARGET_PASSWORD "12345678"
#define UPLOAD_HOST "10.42.0.1"
#define UPLOAD_PORT 8765
#define UPLOAD_PATH "/api/upload"

#define LED_PIN 2

// SD (VSPI)
#define SD_CS 5
#define SD_SCK 18
#define SD_MISO 19
#define SD_MOSI 23

// I2S MIC
#define I2S_WS 25
#define I2S_SD 22
#define I2S_SCK 26
#define I2S_PORT I2S_NUM_0
#define MIC_SAMPLE_RATE 16000
#define MIC_BITS 16
#define MIC_READ_LEN 1024

#define MIC_TRIGGER_PEAK14_LEVEL 3000
#define MIC_TRIGGER_FRAMES 4
#define MIC_COOLDOWN_MS 3000UL
#define AUTO_RECORD_MS 60000UL
#define MIC_SILENCE_STOP_MS 15000UL
#define MIC_VOICE_CONFIRM_FRAMES 3

#define UPLOAD_CHECK_INTERVAL_MS 60000UL

#define MAX_FILE_SIZE 60000000UL
#define MIN_VALID_ENC_REC_SIZE 512UL

#define BIN_MAGIC "PHGCM1"
#define BIN_MAGIC_LEN 6
#define AES_GCM_IV_LEN 12
#define AES_GCM_TAG_LEN 16
#define AES_GCM_BUF_LEN 4096
#define UPLOAD_BUF_LEN 16384

static const uint8_t AES_GCM_KEY[32] = {
    0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77,
    0x88, 0x99, 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff,
    0x10, 0x32, 0x54, 0x76, 0x98, 0xba, 0xdc, 0xfe,
    0xef, 0xcd, 0xab, 0x89, 0x67, 0x45, 0x23, 0x01};

SPIClass sdSpi(VSPI);

bool sdReady = false;
bool micReady = false;
bool micArmed = true;
bool recordingInProgress = false;
unsigned long lastMicTriggerMs = 0;
unsigned long lastUploadCheckMs = 0;
uint32_t micFileCounter = 0;
bool g_recordStopForUpload = false;

// Keep large working buffers out of loopTask stack to avoid stack overflow.
static int32_t g_i2sRecordBuf[MIC_READ_LEN / 4];
static uint8_t g_aesInBuf[AES_GCM_BUF_LEN];
static uint8_t g_aesOutBuf[AES_GCM_BUF_LEN];
static uint8_t g_uploadBuf[UPLOAD_BUF_LEN];

struct MicVoiceStats {
  int peak14;
  bool voiceLike;
};

bool ensureStaConnectedToUploadAP();

String normalizeRecPath(const String &path) {
  String p = path;
  p.replace('\\', '/');
  if (!p.startsWith("/")) p = "/" + p;
  if (!p.startsWith("/rec/")) {
    int slash = p.lastIndexOf('/');
    String base = (slash >= 0) ? p.substring(slash + 1) : p;
    p = "/rec/" + base;
  }
  return p;
}

bool setupSDCard() {
  sdSpi.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);
  if (!SD.begin(SD_CS, sdSpi, 25000000)) {
    return false;
  }
  if (!SD.exists("/rec")) SD.mkdir("/rec");
  return true;
}

void syncMicCounterFromSd() {
  micFileCounter = 0;
  if (!SD.exists("/rec")) return;
  File root = SD.open("/rec");
  if (!root || !root.isDirectory()) {
    if (root) root.close();
    return;
  }
  File f = root.openNextFile();
  while (f) {
    if (!f.isDirectory()) micFileCounter++;
    f.close();
    f = root.openNextFile();
  }
  root.close();
}

String genMicFilename() {
  micFileCounter++;
  char buf[64];
  snprintf(buf, sizeof(buf), "/rec/rec2_no_time_%06lu.wav", (unsigned long)micFileCounter);
  return String(buf);
}

String encryptedPathFromWavPath(const String &wavPath) {
  String encPath = wavPath;
  int dot = encPath.lastIndexOf('.');
  if (dot >= 0) encPath = encPath.substring(0, dot);
  encPath += ".bin";
  return encPath;
}

void setupI2SMic() {
  i2s_config_t i2s_config = {};
  i2s_config.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  i2s_config.sample_rate = MIC_SAMPLE_RATE;
  i2s_config.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  // Read a single channel to reduce duplicated channel noise.
  i2s_config.channel_format = I2S_CHANNEL_FMT_ONLY_RIGHT;
  i2s_config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  i2s_config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  i2s_config.dma_buf_count = 8;
  i2s_config.dma_buf_len = 256;
  i2s_config.use_apll = false;
  i2s_config.tx_desc_auto_clear = false;
  i2s_config.fixed_mclk = 0;

  i2s_pin_config_t pin_config = {};
  pin_config.bck_io_num = I2S_SCK;
  pin_config.ws_io_num = I2S_WS;
  pin_config.data_out_num = I2S_PIN_NO_CHANGE;
  pin_config.data_in_num = I2S_SD;

  if (i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL) != ESP_OK) {
    return;
  }
  if (i2s_set_pin(I2S_PORT, &pin_config) != ESP_OK) {
    return;
  }
  i2s_set_clk(I2S_PORT, MIC_SAMPLE_RATE, I2S_BITS_PER_SAMPLE_32BIT, I2S_CHANNEL_MONO);
  i2s_zero_dma_buffer(I2S_PORT);
  micReady = true;
}

MicVoiceStats readMicVoiceStats() {
  static int32_t i2sData[MIC_READ_LEN / 4];
  size_t bytesRead = 0;
  MicVoiceStats st = {0, false};
  if (i2s_read(I2S_PORT, (void *)i2sData, MIC_READ_LEN, &bytesRead, 20 / portTICK_PERIOD_MS) != ESP_OK) {
    return st;
  }

  int peak = 0;
  int samples = bytesRead / 4;
  if (samples <= 0) return st;

  // Remove frame DC offset to reduce constant hiss/floor bias from MEMS + I2S chain.
  int64_t dcSum = 0;
  for (int i = 0; i < samples; i++) dcSum += i2sData[i];
  int32_t dc = (int32_t)(dcSum / samples);

  for (int i = 0; i < samples; i++) {
    int32_t centered = i2sData[i] - dc;
    int16_t s16 = (int16_t)(centered >> 14);
    int a = abs((int)s16);
    if (a > peak) peak = a;
  }

  st.peak14 = peak;
  st.voiceLike = (peak >= MIC_TRIGGER_PEAK14_LEVEL);
  return st;
}

void writeWavHeader(File &f, uint32_t dataBytes) {
  uint32_t chunkSize = 36 + dataBytes;
  uint16_t audioFormat = 1;
  uint16_t channels = 1;
  uint32_t sampleRate = MIC_SAMPLE_RATE;
  uint16_t bitsPerSample = 16;
  uint16_t blockAlign = channels * (bitsPerSample / 8);
  uint32_t byteRate = sampleRate * blockAlign;
  uint32_t subchunk1Size = 16;

  f.seek(0);
  f.write((const uint8_t *)"RIFF", 4);
  f.write((uint8_t *)&chunkSize, 4);
  f.write((const uint8_t *)"WAVE", 4);
  f.write((const uint8_t *)"fmt ", 4);
  f.write((uint8_t *)&subchunk1Size, 4);
  f.write((uint8_t *)&audioFormat, 2);
  f.write((uint8_t *)&channels, 2);
  f.write((uint8_t *)&sampleRate, 4);
  f.write((uint8_t *)&byteRate, 4);
  f.write((uint8_t *)&blockAlign, 2);
  f.write((uint8_t *)&bitsPerSample, 2);
  f.write((const uint8_t *)"data", 4);
  f.write((uint8_t *)&dataBytes, 4);
}

bool recordTriggeredWavToSD(const char *path, uint32_t durationMs) {
  if (!micReady || !sdReady) return false;

  File audioFile = SD.open(path, FILE_WRITE);
  if (!audioFile) {
    return false;
  }

  uint8_t emptyHeader[44] = {0};
  audioFile.write(emptyHeader, 44);

  recordingInProgress = true;
  uint32_t totalDataBytes = 0;
  uint32_t maxSamplesAllowed = ((MAX_FILE_SIZE - 44UL) / 2UL);
  uint32_t writtenSamples = 0;
  unsigned long start = millis();
  unsigned long lastVoiceMs = start;
  unsigned long lastUploadProbeMs = start;
  int voiceConfirmFrames = 0;
  g_recordStopForUpload = false;

  while ((millis() - start) < durationMs && writtenSamples < maxSamplesAllowed) {
    size_t bytesRead = 0;
    if (i2s_read(I2S_PORT, (void *)g_i2sRecordBuf, MIC_READ_LEN, &bytesRead, portMAX_DELAY) != ESP_OK) continue;

    int peak = 0;
    int samples = bytesRead / 4;
    if (samples <= 0) continue;

    // Remove per-frame DC offset before scaling to 16-bit PCM.
    int64_t dcSum = 0;
    for (int i = 0; i < samples; i++) dcSum += g_i2sRecordBuf[i];
    int32_t dc = (int32_t)(dcSum / samples);

    for (int i = 0; i < samples; i++) {
      int32_t centered = g_i2sRecordBuf[i] - dc;
      int16_t s16 = (int16_t)(centered >> 14);
      int a = abs((int)s16);
      if (a > peak) peak = a;
      audioFile.write((uint8_t *)&s16, sizeof(s16));
      totalDataBytes += 2;
      writtenSamples++;
      if (writtenSamples >= maxSamplesAllowed) break;
    }

    if (peak >= MIC_TRIGGER_PEAK14_LEVEL) {
      voiceConfirmFrames++;
      if (voiceConfirmFrames >= MIC_VOICE_CONFIRM_FRAMES) {
        lastVoiceMs = millis();
        voiceConfirmFrames = MIC_VOICE_CONFIRM_FRAMES;
      }
    } else {
      voiceConfirmFrames = 0;
    }

    // In fixed-interval mode, do not stop early by silence.

    if ((millis() - lastUploadProbeMs) >= UPLOAD_CHECK_INTERVAL_MS) {
      lastUploadProbeMs = millis();
      if (ensureStaConnectedToUploadAP()) {
        g_recordStopForUpload = true;
        break;
      }
    }
  }

  writeWavHeader(audioFile, totalDataBytes);
  audioFile.close();
  recordingInProgress = false;

  return totalDataBytes > 0;
}

bool isValidEncSize(size_t s) {
  return s > MIN_VALID_ENC_REC_SIZE && s <= MAX_FILE_SIZE;
}

bool aesGcmEncryptFile(const String &plainPath, const String &encPath, bool deletePlainAfterEncrypt) {
  if (!sdReady || !SD.exists(plainPath)) return false;
  unsigned long encStartMs = millis();

  File in = SD.open(plainPath, "r");
  if (!in) return false;

  if (SD.exists(encPath)) SD.remove(encPath);
  File out = SD.open(encPath, FILE_WRITE);
  if (!out) {
    in.close();
    return false;
  }

  uint8_t iv[AES_GCM_IV_LEN];
  esp_fill_random(iv, AES_GCM_IV_LEN);
  out.write((const uint8_t *)BIN_MAGIC, BIN_MAGIC_LEN);
  out.write(iv, AES_GCM_IV_LEN);

  mbedtls_gcm_context ctx;
  mbedtls_gcm_init(&ctx);
  int ret = mbedtls_gcm_setkey(&ctx, MBEDTLS_CIPHER_ID_AES, AES_GCM_KEY, 256);
  if (ret == 0) ret = mbedtls_gcm_starts(&ctx, MBEDTLS_GCM_ENCRYPT, iv, AES_GCM_IV_LEN, NULL, 0);

  size_t totalIn = 0;

  while (ret == 0 && in.available()) {
    size_t rd = in.read(g_aesInBuf, AES_GCM_BUF_LEN);
    if (rd == 0) break;
    ret = mbedtls_gcm_update(&ctx, rd, g_aesInBuf, g_aesOutBuf);
    if (ret != 0) break;
    size_t wr = out.write(g_aesOutBuf, rd);
    if (wr != rd) {
      ret = -1;
      break;
    }
    totalIn += rd;
  }

  uint8_t tag[AES_GCM_TAG_LEN];
  if (ret == 0) ret = mbedtls_gcm_finish(&ctx, tag, AES_GCM_TAG_LEN);
  if (ret == 0) {
    size_t wr = out.write(tag, AES_GCM_TAG_LEN);
    if (wr != AES_GCM_TAG_LEN) ret = -1;
  }

  mbedtls_gcm_free(&ctx);
  in.close();
  out.close();

  if (ret != 0 || totalIn == 0) {
    SD.remove(encPath);
    return false;
  }

  File chk = SD.open(encPath, "r");
  size_t encSize = chk ? chk.size() : 0;
  if (chk) chk.close();
  if (encSize <= (BIN_MAGIC_LEN + AES_GCM_IV_LEN + AES_GCM_TAG_LEN)) {
    SD.remove(encPath);
    return false;
  }

  if (deletePlainAfterEncrypt && SD.exists(plainPath)) SD.remove(plainPath);
  unsigned long encElapsedMs = millis() - encStartMs;
  float encKbps = (encElapsedMs > 0) ? ((float)totalIn / 1024.0f) / ((float)encElapsedMs / 1000.0f) : 0.0f;
  Serial.printf("[AES-GCM] OK: %s -> %s size=%lu time=%lums speed=%.1fKB/s\n",
                plainPath.c_str(),
                encPath.c_str(),
                (unsigned long)encSize,
                encElapsedMs,
                encKbps);
  return true;
}

bool ensureStaConnectedToUploadAP() {
  if (WiFi.status() == WL_CONNECTED && WiFi.SSID() == String(TARGET_SSID)) return true;

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.disconnect(false);
  delay(100);
  WiFi.begin(TARGET_SSID, TARGET_PASSWORD);

  int retries = 0;
  while (WiFi.status() != WL_CONNECTED && retries < 40) {
    delay(200);
    retries++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    return true;
  }

  return false;
}

bool uploadFileToHttpApi(const String &localPath, const String &saveName) {
  if (!SD.exists(localPath)) return false;

  File f = SD.open(localPath, "r");
  if (!f || f.isDirectory()) {
    if (f) f.close();
    return false;
  }

  size_t total = f.size();
  if (!isValidEncSize(total)) {
    f.close();
    return false;
  }

  const char *boundary = "----PhantomNode2Boundary7MA4YWxkTrZu0gW";
  String pre = String("--") + boundary + "\r\n" +
               "Content-Disposition: form-data; name=\"file\"; filename=\"" + saveName + "\"\r\n" +
               "Content-Type: application/octet-stream\r\n\r\n";
  String post = String("\r\n--") + boundary + "--\r\n";
  size_t contentLength = pre.length() + total + post.length();

  WiFiClient c;
  unsigned long t0 = millis();
  if (!c.connect(UPLOAD_HOST, UPLOAD_PORT)) {
    f.close();
    return false;
  }
  unsigned long tConnected = millis();
  Serial.printf("[UPLOAD-STEP] tcp_connect file=%s ms=%lu\n", saveName.c_str(), (unsigned long)(tConnected - t0));

  unsigned long uploadStartMs = tConnected;

  c.printf("POST %s HTTP/1.1\r\nHost: %s:%d\r\nContent-Type: multipart/form-data; boundary=%s\r\nContent-Length: %d\r\nConnection: close\r\n\r\n",
           UPLOAD_PATH, UPLOAD_HOST, UPLOAD_PORT, boundary, (int)contentLength);
  unsigned long tHeader = millis();
  Serial.printf("[UPLOAD-STEP] send_header file=%s ms=%lu\n", saveName.c_str(), (unsigned long)(tHeader - tConnected));
  c.print(pre);
  unsigned long tPre = millis();
  Serial.printf("[UPLOAD-STEP] send_multipart_preamble file=%s ms=%lu\n", saveName.c_str(), (unsigned long)(tPre - tHeader));

  size_t sent = 0;
  unsigned long t = millis();
  while (sent < total && c.connected() && (millis() - t) < 90000UL) {
    size_t rd = f.read(g_uploadBuf, min((size_t)UPLOAD_BUF_LEN, total - sent));
    if (rd == 0) break;
    size_t wr = c.write(g_uploadBuf, rd);
    if (wr != rd) break;
    sent += wr;
    t = millis();
  }
  f.close();
  unsigned long tBody = millis();
  Serial.printf("[UPLOAD-STEP] send_body file=%s sent=%lu/%lu ms=%lu\n",
                saveName.c_str(),
                (unsigned long)sent,
                (unsigned long)total,
                (unsigned long)(tBody - tPre));

  if (sent == total) c.print(post);
  unsigned long tPost = millis();
  Serial.printf("[UPLOAD-STEP] send_multipart_end file=%s ms=%lu\n", saveName.c_str(), (unsigned long)(tPost - tBody));

  String resp;
  t = millis();
  while ((c.connected() || c.available()) && (millis() - t) < 10000UL) {
    if (c.available()) {
      resp += (char)c.read();
      t = millis();
    } else {
      delay(2);
    }
  }
  unsigned long tResp = millis();
  Serial.printf("[UPLOAD-STEP] wait_response file=%s ms=%lu\n", saveName.c_str(), (unsigned long)(tResp - tPost));
  c.stop();
  unsigned long uploadElapsedMs = millis() - uploadStartMs;

  bool ok = (sent == total && (resp.indexOf(" 200 ") >= 0 || resp.indexOf(" 201 ") >= 0));
  float kbps = (uploadElapsedMs > 0) ? ((float)sent / 1024.0f) / ((float)uploadElapsedMs / 1000.0f) : 0.0f;
  Serial.printf("[UPLOAD] %s sent=%lu/%lu %s time=%lums speed=%.1fKB/s\n",
                saveName.c_str(),
                (unsigned long)sent,
                (unsigned long)total,
                ok ? "OK" : "FAIL",
                uploadElapsedMs,
                kbps);
  return ok;
}

bool uploadAllRecBins() {
  if (!sdReady) return false;
  if (!ensureStaConnectedToUploadAP()) {
    return false;
  }

  if (!SD.exists("/rec")) return true;

  File root = SD.open("/rec");
  if (!root || !root.isDirectory()) {
    if (root) root.close();
    return false;
  }

  int uploaded = 0;
  int failed = 0;
  File f = root.openNextFile();
  while (f) {
    if (!f.isDirectory()) {
      String fullPath = normalizeRecPath(String(f.name()));
      String base = fullPath.substring(fullPath.lastIndexOf('/') + 1);
      size_t sz = f.size();
      if (base.endsWith(".bin") && isValidEncSize(sz)) {
        bool ok = uploadFileToHttpApi(fullPath, base);
        if (ok) {
          SD.remove(fullPath);
          uploaded++;
        } else {
          failed++;
        }
      }
    }
    f.close();
    f = root.openNextFile();
    delay(1);
  }
  root.close();

  return failed == 0;
}

void handleAutoMicRecord() {
  if (!micReady || !sdReady || recordingInProgress) return;
  if (millis() - lastMicTriggerMs < AUTO_RECORD_MS) return;

  String wavPath = genMicFilename();
  String encPath = encryptedPathFromWavPath(wavPath);
  lastMicTriggerMs = millis();

  bool ok = recordTriggeredWavToSD(wavPath.c_str(), AUTO_RECORD_MS);
  if (ok) {
    bool encOk = aesGcmEncryptFile(wavPath, encPath, true);
    if (encOk && g_recordStopForUpload) {
      uploadAllRecBins();
      g_recordStopForUpload = false;
    }
  }
}

void handleUploadTick() {
  if (recordingInProgress) return;
  if (millis() - lastUploadCheckMs < UPLOAD_CHECK_INTERVAL_MS) return;
  lastUploadCheckMs = millis();
  uploadAllRecBins();
}

void setup() {
  Serial.begin(115200);
  delay(500);

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);


  sdReady = setupSDCard();
  syncMicCounterFromSd();

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.disconnect(false);

  setupI2SMic();

  // Start first 1-minute recording immediately after boot.
  lastMicTriggerMs = millis() - AUTO_RECORD_MS;

  digitalWrite(LED_PIN, HIGH);
}

void loop() {
  handleAutoMicRecord();
  handleUploadTick();
}
