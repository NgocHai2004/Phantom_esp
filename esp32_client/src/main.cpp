/*
 * v2.3 — multi-format upload + SD recorder datetime filename
 * ESP32 NODE-2 — APSTA + SD File Relay
 * ══════════════════════════════════════════════════════════════
 * Vai trò : Kết nối vào Node-1, lấy file về lưu SD,
 *           rồi phát WiFi AP "ESP32-Node-2" để laptop lấy file
 *
 * Logic ghi âm:
 *   Có tiếng to vượt ngưỡng → ghi 30 giây vào SD /rec
 *   Ghi xong → thử đồng bộ Phantom-1; nếu không thấy thì bỏ qua
 *
 * Endpoints (port 80):
 *   GET  /status              ← trạng thái
 *   GET  /file/info           ← thông tin file (audio.wav)
 *   GET  /file/list           ← danh sách tất cả file
 *   GET  /file/download?name= ← download file theo tên (mọi định dạng)
 *   POST /file/upload         ← upload file (X-Filename header + raw body)
 *   POST /file/clear          ← xóa audio.wav
 *   POST /file/delete?name=   ← xóa file theo tên
 *   GET  /ram/info            ← RAM buffer info
 *   POST /sync                ← trigger đồng bộ lại từ Node-1
 *
 * Port 8080: Raw TCP WAV (tương thích firmware cũ)
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <SD.h>
#include <SPI.h>
#include <driver/i2s.h>
#include <FS.h>
#include <time.h>
#include <vector>
#include "test_wav.h"

// ── Cấu hình Node-2 ───────────────────────────────────────────
#define NODE_ID 2

// AP của Node-2 (laptop kết nối vào đây để lấy file)
#define MY_AP_SSID "Phantom-2"
#define MY_AP_PASSWORD "12345678"
#define MY_AP_CHANNEL 6    // Kênh khác Node-1 (channel 1)
#define MY_AP_HIDDEN false // Không ẩn SSID để Node-1 scan được
#define MY_AP_MAX_CON 4

// Node-1 cần kết nối để lấy file
#define NODE1_SSID "Phantom-1"
#define NODE1_PASSWORD "12345678"
#define NODE1_IP "192.168.4.1"
#define NODE1_TCP_PORT 8080
#define NODE1_HTTP_PORT 80

#define LED_PIN 2
#define HTTP_PORT 80
#define AUDIO_PORT 8080
#define UPLOAD_PORT 8081 // Raw TCP upload — bypass WebServer body issue
#define AUDIO_WAV_PATH "/audio.wav"
#define MAX_FILE_SIZE 50000000 // 50 MB for SD
#define MY_AP_IP_STR "192.168.5.1"
#define SYNC_INTERVAL_MS 60000UL

// ── I2S MIC ───────────────────────────────────────────────────
#define I2S_WS 25
#define I2S_SD 22
#define I2S_SCK 26
#define I2S_PORT I2S_NUM_0

#define MIC_SAMPLE_RATE 16000
#define MIC_SAMPLE_BITS 16
#define MIC_I2S_READ_LEN 1024

// ── SD card ───────────────────────────────────────────────────
#define SD_CS 5
#define SD_SCK 18
#define SD_MISO 19
#define SD_MOSI 23

// ── Auto trigger record ───────────────────────────────────────
#define AUTO_RECORD_MS 30000UL

// ── Human voice trigger tuning ────────────────────────────────
// I2S 32-bit -> center DC -> shift về int16 để phân tích.
// peak13 chỉ để xem log so sánh với code cũ; trigger chính dùng rms15.
#define MIC_ANALYZE_SHIFT 15
#define MIC_TRIGGER_RMS_LEVEL 1200    // tăng lên 1800-2500 nếu vẫn tự ghi
#define MIC_REARM_RMS_LEVEL 700       // phải nhỏ hơn trigger
#define MIC_MIN_ZCR 3                 // zero-crossing thấp quá thường là ù/rung DC
#define MIC_MAX_ZCR 90                // cao quá thường là hiss/nhiễu cao tần
#define MIC_VOICE_FRAMES_TO_TRIGGER 8 // ~8 frame liên tục mới ghi
#define MIC_COOLDOWN_MS 2000UL
#define MIC_LOG_INTERVAL_MS 500UL
#define REC_LOG_INTERVAL_MS 1000UL

// Giữ lại define cũ để tránh lỗi nếu còn đoạn nào tham chiếu
#define MIC_TRIGGER_LEVEL 5000
#define MIC_REARM_LEVEL 3000

bool setupSDCard()
{
  SPI.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);
  if (!SD.begin(SD_CS, SPI))
  {
    Serial.println("[SD] Initialization FAILED!");
    return false;
  }

  // Thu muc chua file ghi am tu micro
  if (!SD.exists("/rec"))
  {
    if (SD.mkdir("/rec"))
    {
      Serial.println("[SD] Created /rec folder.");
    }
    else
    {
      Serial.println("[SD] WARNING: Cannot create /rec folder.");
    }
  }

  Serial.println("[SD] Initialization OK.");
  return true;
}

// ── Battery (ADC) ─────────────────────────────────────────────
// GPIO34 = ADC1_CH6, input-only, an toàn dùng với WiFi
// Voltage divider 2 điện trở bằng nhau (100kΩ/100kΩ) → hệ số nhân 2.0
#define BATTERY_ADC_PIN 34
#define BATTERY_DIVIDER 2.0f
#define BATTERY_V_MIN 3.0f
#define BATTERY_V_MAX 4.2f

WebServer server(HTTP_PORT);
WiFiServer audioServer(AUDIO_PORT);
WiFiServer uploadServer(UPLOAD_PORT);
// ── RAM buffer ────────────────────────────────────────────────
uint8_t *ramBuf = nullptr;
size_t ramSize = 0;
bool ramReady = false;
// ── Function prototypes ───────────────────────────────────────
int countSdFiles();
String formatUptime(uint32_t ms);
bool syncFromNode1();
bool isPeerBusy();
void handleBusy();
void handleAutoSync();
// ── State ─────────────────────────────────────────────────────
bool nodeEnabled = true; // Luon bat, da bo chuc nang nut BOOT bat/tat node

// ── MIC/SD state ──────────────────────────────────────────────
bool sdReady = false;
bool micReady = false;
bool micArmed = true;
bool recordingInProgress = false;
uint32_t micFileCounter = 0;
String lastMicWavFile = "none";
unsigned long lastMicTriggerMs = 0;

// ── Sync state ────────────────────────────────────────────────
bool syncDone = false;
bool syncFailed = false;
String syncMsg = "not started";
bool syncInProgress = false;
unsigned long lastAutoSyncMs = 0;

// ── LED ───────────────────────────────────────────────────────
void blinkLED(int times, int ms = 100)
{
  for (int i = 0; i < times; i++)
  {
    digitalWrite(LED_PIN, LOW);
    delay(ms);
    digitalWrite(LED_PIN, HIGH);
    delay(ms);
  }
}

// Da xoa hoan toan chuc nang nut BOOT bat/tat node. Node luon chay.

// ── MIME type lookup ──────────────────────────────────────────
String mimeForExt(const String &ext)
{
  if (ext == ".wav")
    return "audio/wav";
  if (ext == ".mp3")
    return "audio/mpeg";
  if (ext == ".ogg")
    return "audio/ogg";
  if (ext == ".flac")
    return "audio/flac";
  if (ext == ".aac")
    return "audio/aac";
  if (ext == ".png")
    return "image/png";
  if (ext == ".jpg" || ext == ".jpeg")
    return "image/jpeg";
  if (ext == ".gif")
    return "image/gif";
  if (ext == ".bmp")
    return "image/bmp";
  if (ext == ".webp")
    return "image/webp";
  if (ext == ".svg")
    return "image/svg+xml";
  if (ext == ".pdf")
    return "application/pdf";
  if (ext == ".txt")
    return "text/plain";
  if (ext == ".csv")
    return "text/csv";
  if (ext == ".json")
    return "application/json";
  if (ext == ".xml")
    return "application/xml";
  if (ext == ".zip")
    return "application/zip";
  if (ext == ".docx")
    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  if (ext == ".xlsx")
    return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  if (ext == ".bin")
    return "application/octet-stream";
  return "application/octet-stream";
}

// ── Sanitize filename — giữ nguyên extension gốc ─────────────
String sanitizeFilename(const String &nameIn)
{
  String name = nameIn;
  name.trim();
  if (name.length() == 0)
    return "";

  int dotIdx = name.lastIndexOf('.');
  String base = (dotIdx > 0) ? name.substring(0, dotIdx) : name;
  String extLow = (dotIdx > 0) ? name.substring(dotIdx) : "";
  extLow.toLowerCase();

  // Sanitize base: giữ alphanumeric, '-', '_', khoảng trắng→'_'
  String outBase = "";
  bool lastUnderscore = false;
  for (int i = 0; i < (int)base.length() && (int)outBase.length() < 32; i++)
  {
    char c = base[i];
    if (isAlphaNumeric(c) || c == '-')
    {
      outBase += c;
      lastUnderscore = false;
    }
    else if (c == '_' || c == ' ' || c == '.' || c == '(' || c == ')')
    {
      if (!lastUnderscore && outBase.length() > 0)
      {
        outBase += '_';
        lastUnderscore = true;
      }
    }
  }
  while (outBase.length() > 0 && outBase[outBase.length() - 1] == '_')
    outBase.remove(outBase.length() - 1);
  if (outBase.length() == 0)
    return "";

  // Sanitize extension: giữ nguyên (tối đa 8 ký tự sau dấu chấm)
  String outExt = "";
  if (extLow.length() > 1)
  {
    outExt = ".";
    for (int i = 1; i < (int)extLow.length() && (int)outExt.length() < 9; i++)
    {
      char c = extLow[i];
      if (isAlphaNumeric(c))
        outExt += c;
    }
    if (outExt.length() <= 1)
      outExt = "";
  }
  if (outExt.length() == 0)
    outExt = ".bin";

  return outBase + outExt;
}

static uint16_t _fileCounter = 0;
String genAutoFilename()
{
  _fileCounter++;
  char buf[24];
  snprintf(buf, sizeof(buf), "file_%04d.bin", _fileCounter);
  return String(buf);
}

// ── SD helpers (replacing SPIFFS helpers) ─────────────────────
bool sdHasFile(const String &path = AUDIO_WAV_PATH) { return SD.exists(path); }

size_t sdFileSize(const String &path = AUDIO_WAV_PATH)
{
  if (!sdHasFile(path))
    return 0;
  File f = SD.open(path, "r");
  if (!f)
    return 0;
  size_t sz = f.size();
  f.close();
  return sz;
}

bool sdSaveAs(const uint8_t *buf, size_t size, const String &path)
{
  File f = SD.open(path, "w");
  if (!f)
  {
    Serial.printf("[SD] Open '%s' FAILED\n", path.c_str());
    return false;
  }
  size_t wr = f.write(buf, size);
  f.close();
  bool ok = (wr == size);
  if (!ok)
  {
    SD.remove(path);
    Serial.printf("[SD] SaveAs '%s' FAILED (%d/%d)\n", path.c_str(), wr, size);
  }
  else
  {
    Serial.printf("[SD] SaveAs '%s' %d/%d → OK\n", path.c_str(), wr, size);
  }
  return ok;
}

bool sdLoadToRam()
{
  if (!sdHasFile())
    return false;
  File f = SD.open(AUDIO_WAV_PATH, "r");
  if (!f)
    return false;
  size_t sz = f.size();
  if (sz == 0 || sz > 2000000)
  {
    f.close();
    return false;
  }
  if (ramBuf)
  {
    free(ramBuf);
    ramBuf = nullptr;
    ramSize = 0;
  }
  ramBuf = (uint8_t *)malloc(sz);
  if (!ramBuf)
  {
    f.close();
    Serial.println("[SD] OOM loading to RAM");
    return false;
  }
  size_t rd = f.read(ramBuf, sz);
  f.close();
  ramSize = rd;
  ramReady = (rd >= 44);
  Serial.printf("[SD] Load %d bytes to RAM → %s\n", rd, ramReady ? "OK" : "FAIL");
  return ramReady;
}

// ── I2S Mic Functions ─────────────────────────────────────────
void setupI2SMic()
{
  i2s_config_t i2s_config = {
      .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
      .sample_rate = MIC_SAMPLE_RATE,
      .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
      .channel_format = I2S_CHANNEL_FMT_ONLY_RIGHT,
      .communication_format = I2S_COMM_FORMAT_STAND_I2S,
      .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
      .dma_buf_count = 8,
      .dma_buf_len = 256,
      .use_apll = false,
      .tx_desc_auto_clear = false,
      .fixed_mclk = 0};
  i2s_pin_config_t pin_config = {
      .bck_io_num = I2S_SCK,
      .ws_io_num = I2S_WS,
      .data_out_num = I2S_PIN_NO_CHANGE,
      .data_in_num = I2S_SD};

  if (i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL) != ESP_OK)
    return;
  if (i2s_set_pin(I2S_PORT, &pin_config) != ESP_OK)
    return;
  micReady = true;
  Serial.println("[MIC] I2S initialized.");
}

String genMicFilename()
{
  struct tm timeinfo;

  // Neu ESP32 da lay duoc gio NTP thi dung ten file theo ngay-gio
  if (getLocalTime(&timeinfo))
  {
    char buf[64];
    strftime(buf, sizeof(buf), "/rec/rec_%Y%m%d_%H%M%S.wav", &timeinfo);
    return String(buf);
  }

  // Fallback khi chua co internet/gio NTP
  micFileCounter++;
  char buf[64];
  snprintf(buf, sizeof(buf), "/rec/rec_no_time_%06lu.wav", (unsigned long)micFileCounter);
  return String(buf);
}

struct MicVoiceStats
{
  int peak13;
  int peak15;
  int rms15;
  int zcr;
  bool voiceLike;
};

MicVoiceStats readMicVoiceStats()
{
  MicVoiceStats st = {0, 0, 0, 0, false};

  if (!micReady || recordingInProgress)
    return st;

  uint8_t i2sData[MIC_I2S_READ_LEN];
  size_t bytesRead = 0;

  if (i2s_read(I2S_PORT, (void *)i2sData, MIC_I2S_READ_LEN, &bytesRead, 20 / portTICK_PERIOD_MS) != ESP_OK)
    return st;
  if (bytesRead == 0)
    return st;

  int32_t *samples32 = (int32_t *)i2sData;
  int sampleCount = bytesRead / 4;
  if (sampleCount <= 0)
    return st;

  // Tính DC offset để loại bỏ lệch nền của I2S mic
  int64_t sum = 0;
  for (int i = 0; i < sampleCount; i++)
    sum += samples32[i];
  int32_t dc = (int32_t)(sum / sampleCount);

  int64_t sumSq15 = 0;
  int prevSign = 0;
  int zcr = 0;

  for (int i = 0; i < sampleCount; i++)
  {
    int32_t centered = samples32[i] - dc;

    // peak13: giống scale cũ của bạn, dùng để xem vì sao code cũ dễ vượt 5000
    int32_t s13 = centered >> 13;
    if (s13 > 32767)
      s13 = 32767;
    if (s13 < -32768)
      s13 = -32768;
    int a13 = abs((int)s13);
    if (a13 > st.peak13)
      st.peak13 = a13;

    // peak15/rms15: scale ít nhạy hơn, dùng cho detect tiếng giống người
    int32_t s15 = centered >> MIC_ANALYZE_SHIFT;
    if (s15 > 32767)
      s15 = 32767;
    if (s15 < -32768)
      s15 = -32768;

    int a15 = abs((int)s15);
    if (a15 > st.peak15)
      st.peak15 = a15;

    sumSq15 += (int64_t)s15 * (int64_t)s15;

    // Zero crossing: giọng người thường có số lần đổi dấu vừa phải.
    // Bỏ qua biên độ quá nhỏ để noise nền không làm zcr tăng giả.
    int sign = 0;
    if (s15 > 100)
      sign = 1;
    else if (s15 < -100)
      sign = -1;

    if (sign != 0)
    {
      if (prevSign != 0 && sign != prevSign)
        zcr++;
      prevSign = sign;
    }
  }

  st.rms15 = (int)sqrt((float)sumSq15 / (float)sampleCount);
  st.zcr = zcr;

  // Điều kiện "giống tiếng người":
  // 1) RMS đủ lớn, không dùng peak đơn lẻ
  // 2) ZCR nằm trong vùng vừa phải để tránh tiếng cạch/spike/hiss
  st.voiceLike = (st.rms15 >= MIC_TRIGGER_RMS_LEVEL &&
                  st.zcr >= MIC_MIN_ZCR &&
                  st.zcr <= MIC_MAX_ZCR);

  return st;
}

// Wrapper giữ tên cũ để những chỗ rearm/debug vẫn dùng được nếu cần.
// Hàm này trả peak theo scale >>15, không còn dùng scale >>13 để trigger.
int readMicPeakLevel()
{
  MicVoiceStats st = readMicVoiceStats();
  return st.peak15;
}

bool recordTriggeredWavToSD(const char *path, uint32_t durationMs = AUTO_RECORD_MS)
{
  if (!sdReady || !micReady || recordingInProgress)
    return false;

  recordingInProgress = true;

  if (!SD.exists("/rec"))
  {
    SD.mkdir("/rec");
  }

  if (SD.exists(path))
    SD.remove(path);

  File audioFile = SD.open(path, FILE_WRITE);
  if (!audioFile)
  {
    recordingInProgress = false;
    Serial.printf("[MIC] Cannot open file for recording: %s\n", path);
    return false;
  }

  // Ghi header rong truoc, sau khi thu xong se quay lai cap nhat header WAV
  uint8_t emptyHeader[44] = {0};
  audioFile.write(emptyHeader, 44);

  i2s_zero_dma_buffer(I2S_PORT);
  delay(50);

  uint8_t i2sData[MIC_I2S_READ_LEN];
  size_t bytesRead = 0;
  uint32_t totalDataBytes = 0;
  uint32_t samplesRecorded = 0;
  uint32_t totalSamplesNeeded = (MIC_SAMPLE_RATE * durationMs) / 1000UL;

  int recPeak14 = 0;
  int recPeak15 = 0;
  unsigned long lastRecLogMs = millis();

  digitalWrite(LED_PIN, HIGH);
  Serial.printf("[MIC] REC START: %s\n", path);

  while (samplesRecorded < totalSamplesNeeded)
  {
    server.handleClient();
    if (!nodeEnabled)
      break;

    if (i2s_read(I2S_PORT, (void *)i2sData, MIC_I2S_READ_LEN, &bytesRead, portMAX_DELAY) != ESP_OK)
    {
      Serial.println("[MIC] i2s_read failed");
      break;
    }

    int32_t *samples32 = (int32_t *)i2sData;
    int sampleCount = bytesRead / 4;

    // Kieu ghi am giong code test: 32-bit I2S -> shift >> 14 -> int16 WAV
    // Đồng thời log peak khi đang ghi để biết file ghi có biên độ bao nhiêu.
    for (int i = 0; i < sampleCount && samplesRecorded < totalSamplesNeeded; i++)
    {
      int32_t raw = samples32[i];

      int32_t s14 = raw >> 14;
      if (s14 > 32767)
        s14 = 32767;
      if (s14 < -32768)
        s14 = -32768;

      int32_t s15 = raw >> 15;
      if (s15 > 32767)
        s15 = 32767;
      if (s15 < -32768)
        s15 = -32768;

      int a14 = abs((int)s14);
      int a15 = abs((int)s15);
      if (a14 > recPeak14)
        recPeak14 = a14;
      if (a15 > recPeak15)
        recPeak15 = a15;

      int16_t s16 = (int16_t)s14;
      audioFile.write((uint8_t *)&s16, sizeof(s16));

      totalDataBytes += sizeof(s16);
      samplesRecorded++;
    }

    if (millis() - lastRecLogMs >= REC_LOG_INTERVAL_MS)
    {
      Serial.printf("[MIC] REC LOG peak14=%d peak15=%d samples=%lu/%lu\n",
                    recPeak14, recPeak15,
                    (unsigned long)samplesRecorded,
                    (unsigned long)totalSamplesNeeded);
      recPeak14 = 0;
      recPeak15 = 0;
      lastRecLogMs = millis();
    }
  }

  // Tao WAV header PCM 16-bit mono
  uint32_t chunkSize = 36 + totalDataBytes;
  uint32_t subchunk1Size = 16;
  uint16_t audioFormat = 1;
  uint16_t channels = 1;
  uint32_t sampleRate = MIC_SAMPLE_RATE;
  uint16_t bitsPerSample = MIC_SAMPLE_BITS;
  uint32_t byteRate = sampleRate * channels * bitsPerSample / 8;
  uint16_t blockAlign = channels * bitsPerSample / 8;

  audioFile.seek(0);
  audioFile.write((const uint8_t *)"RIFF", 4);
  audioFile.write((uint8_t *)&chunkSize, 4);
  audioFile.write((const uint8_t *)"WAVE", 4);
  audioFile.write((const uint8_t *)"fmt ", 4);
  audioFile.write((uint8_t *)&subchunk1Size, 4);
  audioFile.write((uint8_t *)&audioFormat, 2);
  audioFile.write((uint8_t *)&channels, 2);
  audioFile.write((uint8_t *)&sampleRate, 4);
  audioFile.write((uint8_t *)&byteRate, 4);
  audioFile.write((uint8_t *)&blockAlign, 2);
  audioFile.write((uint8_t *)&bitsPerSample, 2);
  audioFile.write((const uint8_t *)"data", 4);
  audioFile.write((uint8_t *)&totalDataBytes, 4);

  audioFile.close();
  digitalWrite(LED_PIN, LOW);

  recordingInProgress = false;

  Serial.printf("[MIC] REC DONE: %s (%lu bytes audio)\n", path, (unsigned long)totalDataBytes);
  return totalDataBytes > 0;
}

void handleAutoMicRecord()
{
  if (!micReady || !sdReady || recordingInProgress || !micArmed)
    return;

  if (millis() - lastMicTriggerMs < MIC_COOLDOWN_MS)
    return;

  static int voiceFrames = 0;
  static unsigned long lastLogMs = 0;

  MicVoiceStats st = readMicVoiceStats();

  if (millis() - lastLogMs >= MIC_LOG_INTERVAL_MS)
  {
    Serial.printf("[MIC] peak13=%d peak15=%d rms15=%d zcr=%d voice=%s frames=%d/%d\n",
                  st.peak13, st.peak15, st.rms15, st.zcr,
                  st.voiceLike ? "yes" : "no",
                  voiceFrames, MIC_VOICE_FRAMES_TO_TRIGGER);
    lastLogMs = millis();
  }

  if (st.voiceLike)
  {
    voiceFrames++;
  }
  else
  {
    voiceFrames = 0;
  }

  if (voiceFrames >= MIC_VOICE_FRAMES_TO_TRIGGER)
  {
    String path = genMicFilename();
    lastMicWavFile = path;
    lastMicTriggerMs = millis();
    micArmed = false;
    voiceFrames = 0;

    Serial.printf("[FLOW] Human-like voice trigger peak13=%d peak15=%d rms15=%d zcr=%d\n",
                  st.peak13, st.peak15, st.rms15, st.zcr);

    bool ok = recordTriggeredWavToSD(path.c_str(), AUTO_RECORD_MS);

    if (ok)
    {
      Serial.println("[FLOW] Record done -> sync Phantom-1 once");
      syncDone = syncFromNode1();
      syncFailed = !syncDone;

      if (syncDone)
        Serial.println("[FLOW] Sync Phantom-1 OK");
      else
        Serial.println("[FLOW] Phantom-1 not found or sync failed -> skip");
    }
    else
    {
      Serial.println("[FLOW] Record failed -> skip sync");
    }
  }
}

void handleBusy()
{
  bool busy = recordingInProgress || syncInProgress;
  String reason = recordingInProgress ? "recording" : (syncInProgress ? "syncing" : "idle");
  String j = "{\"node\":2";
  j += ",\"busy\":" + String(busy ? "true" : "false");
  j += ",\"recording\":" + String(recordingInProgress ? "true" : "false");
  j += ",\"sync_in_progress\":" + String(syncInProgress ? "true" : "false");
  j += ",\"reason\":\"" + reason + "\"";
  j += ",\"sync_msg\":\"" + syncMsg + "\"";
  j += ",\"last_mic_file\":\"" + lastMicWavFile + "\"}";
  server.send(busy ? 423 : 200, "application/json", j);
}

void handleAutoSync()
{
  if (millis() - lastAutoSyncMs < SYNC_INTERVAL_MS)
    return;

  lastAutoSyncMs = millis();

  if (!sdReady || recordingInProgress || syncInProgress)
  {
    if (recordingInProgress)
      syncMsg = "skip: recording in progress";
    return;
  }

  Serial.println("[AutoSync] 60s tick -> try sync from Phantom-1");
  syncDone = syncFromNode1();
  syncFailed = !syncDone;
}

void handleSyncStatus()
{
  int count = countSdFiles();
  String j = "{\"node\":2";
  j += ",\"file_count\":" + String(count);
  j += ",\"sd_used\":" + String(sdReady ? (uint32_t)(SD.usedBytes() / 1024) : 0) + " KB";
  j += ",\"sd_free\":" + String(sdReady ? (uint32_t)((SD.totalBytes() - SD.usedBytes()) / 1024) : 0) + " KB";
  j += ",\"uptime\":\"" + formatUptime(millis()) + "\"";
  j += ",\"free_heap\":" + String(ESP.getFreeHeap());
  j += ",\"last_mic_file\":\"" + lastMicWavFile + "\"}";
  server.send(200, "application/json", j);
}

void handleSdList()
{
  if (!sdReady)
  {
    server.send(500, "application/json", "{\"error\":\"sd not ready\"}");
    return;
  }
  File root = SD.open("/rec");
  if (!root || !root.isDirectory())
  {
    server.send(200, "application/json", "{\"files\":[],\"count\":0}");
    return;
  }
  String j = "{\"files\":[";
  int count = 0;
  File f = root.openNextFile();
  while (f)
  {
    if (!f.isDirectory())
    {
      if (count > 0)
        j += ",";
      j += "{\"name\":\"" + String(f.name()) + "\",\"size\":" + String((uint32_t)f.size()) + "}";
      count++;
    }
    f.close();
    f = root.openNextFile();
  }
  j += "],\"count\":" + String(count) + "}";
  server.send(200, "application/json", j);
}

void handleSdDownload()
{
  String name = server.arg("name");
  if (!name.startsWith("/"))
    name = "/rec/" + name;
  if (!SD.exists(name))
  {
    server.send(404, "text/plain", "File not found");
    return;
  }
  File f = SD.open(name, "r");
  server.streamFile(f, "audio/wav");
  f.close();
}
String formatUptime(uint32_t ms)
{
  uint32_t s = ms / 1000, m = s / 60;
  s %= 60;
  uint32_t h = m / 60;
  m %= 60;
  char b[32];
  snprintf(b, sizeof(b), "%02d:%02d:%02d", h, m, s);
  return String(b);
}

String wavInfoJson(const uint8_t *buf, size_t size)
{
  if (!buf || size < 44)
    return "{}";
  if (buf[0] != 'R' || buf[1] != 'I' || buf[2] != 'F' || buf[3] != 'F')
    return "{\"is_wav\":false}";
  if (buf[8] != 'W' || buf[9] != 'A' || buf[10] != 'V' || buf[11] != 'E')
    return "{\"is_wav\":false}";
  uint16_t fmt = buf[20] | (buf[21] << 8);
  uint16_t ch = buf[22] | (buf[23] << 8);
  uint32_t sr = buf[24] | (buf[25] << 8) | (buf[26] << 16) | (buf[27] << 24);
  uint16_t bps = buf[34] | (buf[35] << 8);
  uint32_t dsz = buf[40] | (buf[41] << 8) | (buf[42] << 16) | (buf[43] << 24);
  float dur = (sr > 0 && ch > 0 && bps > 0) ? (float)dsz / (sr * ch * (bps / 8)) : 0.0f;
  String j = "{\"is_wav\":true";
  j += ",\"format\":\"" + String(fmt == 1 ? "PCM" : fmt == 3 ? "FLOAT"
                                                             : "OTHER") +
       "\"";
  j += ",\"channels\":" + String(ch);
  j += ",\"sample_rate\":" + String(sr);
  j += ",\"bits_per_sample\":" + String(bps);
  j += ",\"data_size\":" + String(dsz);
  j += ",\"duration_sec\":" + String(dur, 2) + "}";
  return j;
}

// ── Helper: HTTP GET text từ Node-1 (buffer 1024B, nối nhanh) ─
String httpGetFromNode1(const char *path, int timeoutMs = 6000)
{
  WiFiClient c;
  if (!c.connect(NODE1_IP, NODE1_HTTP_PORT))
    return "";
  c.printf("GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n", path, NODE1_IP);
  int contentLength = -1;
  unsigned long t = millis();
  while (c.connected() && (millis() - t) < (unsigned long)timeoutMs)
  {
    if (!c.available())
    {
      delay(1);
      continue;
    }
    String line = c.readStringUntil('\n');
    line.trim();
    if (line.length() == 0)
      break;
    String lo = line;
    lo.toLowerCase();
    if (lo.startsWith("content-length:"))
    {
      String val = line.substring(line.indexOf(':') + 1);
      val.trim();
      contentLength = val.toInt();
    }
    t = millis();
  }
  String body = "";
  // Tăng buffer đọc lên 1024B để giảm số vòng lặp
  uint8_t buf[1024];
  if (contentLength > 0)
    body.reserve(contentLength + 4);
  t = millis();
  while ((c.connected() || c.available()) && (millis() - t) < (unsigned long)timeoutMs)
  {
    size_t av = c.available();
    if (av > 0)
    {
      size_t rd = c.read(buf, min(av, (size_t)1024));
      // concat() nhanh hơn += từng char
      body.concat((const char *)buf, rd);
      t = millis();
      if (contentLength > 0 && (int)body.length() >= contentLength)
        break;
    }
    else
    {
      delay(1);
    }
  }
  c.stop();
  return body;
}

// ── Helper: download 1 file từ Node-1 qua WiFiClient đã connect ─
// persistent=true: dùng lại connection đã có (keep-alive)
// persistent=false: tự connect mới (dùng độc lập)
bool httpDownloadFileFromNode1(const String &filename,
                               WiFiClient *persist = nullptr)
{
  String path = "/file/download?name=" + filename;

  WiFiClient _own;
  WiFiClient *c = persist ? persist : &_own;

  // Nếu không dùng keep-alive → connect mới
  if (!persist)
  {
    if (!c->connect(NODE1_IP, NODE1_HTTP_PORT))
    {
      Serial.printf("[Sync] HTTP connect FAILED for '%s'\n", filename.c_str());
      return false;
    }
  }
  else if (!c->connected())
  {
    // Reconnect nếu connection bị drop
    c->stop();
    if (!c->connect(NODE1_IP, NODE1_HTTP_PORT))
    {
      Serial.printf("[Sync] HTTP reconnect FAILED for '%s'\n", filename.c_str());
      return false;
    }
  }

  // Gửi request — keep-alive để tái dùng TCP
  c->printf("GET %s HTTP/1.1\r\nHost: %s\r\nConnection: keep-alive\r\n\r\n",
            path.c_str(), NODE1_IP);

  // Đọc HTTP headers
  int contentLength = 0;
  bool is200 = false;
  unsigned long t = millis();
  while (c->connected() && (millis() - t) < 8000)
  {
    if (!c->available())
    {
      delay(1);
      continue;
    }
    String line = c->readStringUntil('\n');
    line.trim();
    if (line.length() == 0)
      break;
    String lo = line;
    lo.toLowerCase();
    if (lo.startsWith("http/"))
    {
      is200 = (lo.indexOf(" 200") >= 0);
      if (!is200)
      {
        if (!persist)
          c->stop();
        Serial.printf("[Sync] Non-200 for '%s': %s\n", filename.c_str(), line.c_str());
        return false;
      }
    }
    if (lo.startsWith("content-length:"))
      contentLength = line.substring(line.indexOf(':') + 1).toInt();
    t = millis();
  }
  if (contentLength <= 0 || contentLength > (int)MAX_FILE_SIZE)
  {
    if (!persist)
      c->stop();
    Serial.printf("[Sync] Bad CL=%d for '%s'\n", contentLength, filename.c_str());
    return false;
  }

  // Mở file SD để ghi streaming
  String sdPath = "/" + filename;
  if (SD.exists(sdPath))
    SD.remove(sdPath);
  File f = SD.open(sdPath, "w");
  if (!f)
  {
    if (!persist)
      c->stop();
    Serial.printf("[Sync] SD open FAILED for '%s'\n", filename.c_str());
    return false;
  }

  // Stream TCP -> SD chunk 4096B (tăng từ 512B → 4× nhanh hơn)
  uint8_t chunk[4096];
  size_t rx = 0;
  t = millis();
  while (rx < (size_t)contentLength && (c->connected() || c->available()) && (millis() - t) < 45000)
  {
    size_t av = c->available();
    if (av > 0)
    {
      size_t want = min(av, min((size_t)4096, (size_t)(contentLength - rx)));
      size_t rd = c->readBytes(chunk, want);
      if (rd > 0)
      {
        f.write(chunk, rd);
        rx += rd;
        t = millis();
      }
    }
    else
    {
      delay(1);
    }
  }
  f.close();
  if (!persist)
    c->stop();

  Serial.printf("[Sync] '%s' rx=%d/%d bytes\n", filename.c_str(), rx, contentLength);

  // Xác minh kích thước
  bool saved = false;
  if (rx > 0 && SD.exists(sdPath))
  {
    File chk = SD.open(sdPath, "r");
    if (chk)
    {
      saved = ((size_t)chk.size() == rx);
      chk.close();
    }
  }
  if (!saved)
  {
    SD.remove(sdPath);
    Serial.printf("[Sync] '%s' verify FAIL → xóa\n", filename.c_str());
    return false;
  }

  // Nếu là audio.wav → load vào RAM
  if (filename == "audio.wav" && rx <= 2000000)
  {
    if (ESP.getFreeHeap() > (int)rx + 32768)
    {
      if (ramBuf)
      {
        free(ramBuf);
        ramBuf = nullptr;
        ramSize = 0;
        ramReady = false;
      }
      File fw = SD.open(sdPath, "r");
      if (fw)
      {
        ramBuf = (uint8_t *)malloc(rx);
        if (ramBuf)
        {
          size_t rd = fw.read(ramBuf, rx);
          fw.close();
          ramSize = rd;
          ramReady = (rd >= 44);
        }
        else
          fw.close();
      }
    }
  }

  Serial.printf("[Sync] '%s' %d bytes → OK  heap=%d\n",
                filename.c_str(), rx, ESP.getFreeHeap());
  return true;
}

// ── Lấy size của 1 file từ JSON list Node-1 ──────────────────
// Trả về -1 nếu không tìm thấy
int32_t getRemoteFileSize(const String &listJson, const String &fname)
{
  // Tìm block {"name":"<fname>", ..., "size":<N>, ...}
  int pos = 0;
  while (true)
  {
    int ni = listJson.indexOf("\"name\":\"", pos);
    if (ni < 0)
      break;
    ni += 8;
    int ne = listJson.indexOf("\"", ni);
    if (ne < 0)
      break;
    String n = listJson.substring(ni, ne);
    if (n == fname)
    {
      // Tìm "size": trong đoạn gần đó
      int si = listJson.indexOf("\"size\":", ne);
      if (si < 0)
        return -1;
      si += 7;
      // skip khoảng trắng
      while (si < (int)listJson.length() && listJson[si] == ' ')
        si++;
      String numStr = "";
      while (si < (int)listJson.length() && isDigit(listJson[si]))
      {
        numStr += listJson[si];
        si++;
      }
      return numStr.toInt();
    }
    pos = ne + 1;
  }
  return -1;
}
void restorePhantom2AP()
{
  WiFi.mode(WIFI_AP_STA);

  IPAddress apIP(192, 168, 5, 1);
  IPAddress gw(192, 168, 5, 1);
  IPAddress sn(255, 255, 255, 0);

  WiFi.softAPConfig(apIP, gw, sn);
  WiFi.softAP(MY_AP_SSID, MY_AP_PASSWORD, MY_AP_CHANNEL, MY_AP_HIDDEN, MY_AP_MAX_CON);

  server.begin();
  audioServer.begin();
  uploadServer.begin();

  Serial.println("[WiFi] Phantom-2 AP restored");
}

// ── Check Phantom-1 busy before sync ──────────────────────────
bool isPeerBusy()
{
  String body = httpGetFromNode1("/busy", 3000);
  if (body.length() == 0)
  {
    // Firmware cu chua co /busy: fallback check /status.
    body = httpGetFromNode1("/status", 3000);
  }

  if (body.indexOf("\"busy\":true") >= 0 ||
      body.indexOf("\"recording\":true") >= 0 ||
      body.indexOf("\"sync_in_progress\":true") >= 0)
  {
    Serial.println("[Sync] Phantom-1 is BUSY -> skip this sync");
    syncMsg = "skip: Phantom-1 busy";
    return true;
  }

  return false;
}

// ── Đồng bộ TẤT CẢ file từ Node-1 (keep-alive TCP, chunk 4096B) ─
bool syncFromNode1()
{
  if (recordingInProgress)
  {
    syncMsg = "skip: recording in progress";
    Serial.println("[Sync] Local recording -> skip sync");
    return false;
  }
  if (syncInProgress)
  {
    syncMsg = "busy: Phantom-2 already syncing";
    Serial.println("[Sync] Local sync busy -> skip");
    return false;
  }

  syncInProgress = true;
  struct SyncBusyGuard
  {
    ~SyncBusyGuard() { syncInProgress = false; }
  } syncBusyGuard;

  Serial.println("\n[Sync] ══ Bắt đầu kết nối Thiết bị A (Node-1) ══");
  syncMsg = "connecting Phantom-1";

  WiFi.begin(NODE1_SSID, NODE1_PASSWORD);
  int retries = 0;
  while (WiFi.status() != WL_CONNECTED && retries < 12)
  {
    unsigned long tw = millis();
    while (millis() - tw < 300)
    {
      server.handleClient();
      delay(5);
    }
    Serial.print(".");
    retries++;
  }
  if (WiFi.status() != WL_CONNECTED)
  {
    Serial.println("\n[Sync] THẤT BẠI: Không tìm thấy Thiết bị A");
    syncMsg = "failed: Node-1 not found";
    WiFi.disconnect(false);
    return false;
  }
  Serial.printf("\n[Sync] Đã kết nối Thiết bị A  IP: %s\n",
                WiFi.localIP().toString().c_str());
  delay(100); // giảm từ 300ms → 100ms

  if (isPeerBusy())
  {
    WiFi.disconnect(false);
    restorePhantom2AP();
    return false;
  }

  // Lấy danh sách file từ Node-1 — retry 3 lần
  String listJson = "";
  for (int attempt = 0; attempt < 3; attempt++)
  {
    listJson = httpGetFromNode1("/file/list", 4000);
    if (listJson.length() > 10 && listJson.indexOf("\"name\"") >= 0)
      break;
    Serial.printf("[Sync] /file/list trống (lần %d/3) — thử lại...\n", attempt + 1);
    unsigned long tw = millis();
    while (millis() - tw < 300)
    {
      server.handleClient();
      delay(5);
    }
  }

  // Đếm file Node-1
  int node1Count = 0;
  {
    int p = 0;
    while (listJson.indexOf("\"name\":\"", p) >= 0)
    {
      int ni = listJson.indexOf("\"name\":\"", p) + 8;
      int ne = listJson.indexOf("\"", ni);
      if (ne < 0)
        break;
      node1Count++;
      p = ne + 1;
    }
  }
  Serial.printf("[Sync] Danh sách: %d file (Thiết bị A)\n", node1Count);

  if (listJson.indexOf("\"count\":0") >= 0 || listJson.indexOf("\"files\":[]") >= 0 || node1Count == 0)
  {
    Serial.println("[Sync] Thiết bị A không có file — bỏ qua");
    restorePhantom2AP();
    delay(100);
    syncMsg = "ok: node-1 empty";
    return false;
  }

  // Parse tên file từ Node-1
  std::vector<String> remoteFiles;
  int pos = 0;
  while (true)
  {
    int ni = listJson.indexOf("\"name\":\"", pos);
    if (ni < 0)
      break;
    ni += 8;
    int ne = listJson.indexOf("\"", ni);
    if (ne < 0)
      break;
    String fname = listJson.substring(ni, ne);
    if (fname.length() > 0)
      remoteFiles.push_back(fname);
    pos = ne + 1;
  }

  int downloaded = 0;
  int skipped = 0;
  int updated = 0;

  // ── Keep-alive: 1 WiFiClient cho tất cả file download ────────
  WiFiClient keepAlive;
  if (!keepAlive.connect(NODE1_IP, NODE1_HTTP_PORT))
  {
    Serial.println("[Sync] Keep-alive connect FAILED — fallback per-file");
    keepAlive.stop();
  }
  bool useKeepAlive = keepAlive.connected();
  Serial.printf("[Sync] Keep-alive: %s\n", useKeepAlive ? "ON" : "OFF (fallback)");

  for (auto &fname : remoteFiles)
  {
    String path = "/" + fname;
    int32_t remoteSize = getRemoteFileSize(listJson, fname);

    if (SD.exists(path))
    {
      File f = SD.open(path, "r");
      size_t localSize = f ? f.size() : 0;
      if (f)
        f.close();

      if (remoteSize > 0 && (int32_t)localSize == remoteSize)
      {
        Serial.printf("[Sync] Bỏ qua '%s' — đã có (%d bytes)\n", fname.c_str(), (int)localSize);
        skipped++;
        continue;
      }
      Serial.printf("[Sync] Cập nhật '%s' — local=%d remote=%d bytes\n",
                    fname.c_str(), (int)localSize, (int)remoteSize);
      SD.remove(path);
      bool ok = httpDownloadFileFromNode1(fname, useKeepAlive ? &keepAlive : nullptr);
      if (ok)
      {
        downloaded++;
        updated++;
      }
    }
    else
    {
      Serial.printf("[Sync] Tải mới '%s' (%d bytes)\n", fname.c_str(), remoteSize);
      bool ok = httpDownloadFileFromNode1(fname, useKeepAlive ? &keepAlive : nullptr);
      if (ok)
        downloaded++;
    }
    // Không delay(80) — tiết kiệm ~80ms × N file
  }

  if (useKeepAlive)
    keepAlive.stop();
  if (downloaded > 0)
    blinkLED(5, 100);

  WiFi.disconnect(false);
  delay(100); // giảm từ 300ms → 100ms
  Serial.printf("[Sync] Đã ngắt kết nối Thiết bị A. AP vẫn chạy.\n");
  Serial.printf("[Sync] Kết quả: tải %d, cập nhật %d, bỏ qua %d / %d file\n",
                downloaded, updated, skipped, (int)remoteFiles.size());

  // Đảm bảo ramBuf có file WAV nếu chưa có
  if (!ramReady && !remoteFiles.empty())
  {
    for (auto &fname : remoteFiles)
    {
      String fl = fname;
      fl.toLowerCase();
      if (!fl.endsWith(".wav"))
        continue;
      String firstFile = "/" + fname;
      if (SD.exists(firstFile))
      {
        File f = SD.open(firstFile, "r");
        if (f)
        {
          size_t sz = f.size();
          if (sz <= 2000000)
          {
            if (ramBuf)
            {
              free(ramBuf);
              ramBuf = nullptr;
            }
            ramBuf = (uint8_t *)malloc(sz);
            if (ramBuf)
            {
              size_t rd = f.read(ramBuf, sz);
              f.close();
              ramSize = rd;
              ramReady = (rd >= 44);
            }
            else
              f.close();
          }
          else
            f.close();
          if (ramReady)
            break;
        }
      }
    }
  }

  syncMsg = "ok: synced " + String(downloaded) + "/" + String(remoteFiles.size()) + " files";
  return (downloaded > 0);
}

// ── Battery helpers ───────────────────────────────────────────
float readBatteryVoltage()
{
  uint32_t sum = 0;
  for (int i = 0; i < 16; i++)
  {
    sum += analogRead(BATTERY_ADC_PIN);
    delayMicroseconds(200);
  }
  float raw = sum / 16.0f;
  float vAdc = (raw / 4095.0f) * 3.3f;
  return vAdc * BATTERY_DIVIDER;
}
int batteryPercent(float v)
{
  if (v <= BATTERY_V_MIN)
    return 0;
  if (v >= BATTERY_V_MAX)
    return 100;
  return (int)(((v - BATTERY_V_MIN) / (BATTERY_V_MAX - BATTERY_V_MIN)) * 100.0f + 0.5f);
}
int readBatteryRaw()
{
  uint32_t sum = 0;
  for (int i = 0; i < 16; i++)
  {
    sum += analogRead(BATTERY_ADC_PIN);
    delayMicroseconds(200);
  }
  return (int)(sum / 16);
}

// ── HTTP Handlers ─────────────────────────────────────────────

void handleStatus()
{
  float _bv = readBatteryVoltage();
  int _bp = batteryPercent(_bv);
  int _br = readBatteryRaw();
  server.send(200, "application/json",
              String("{\"node\":2") +
                  ",\"ap_ssid\":\"" + MY_AP_SSID + "\"" +
                  ",\"ap_ip\":\"" + MY_AP_IP_STR + "\"" +
                  ",\"uptime\":\"" + formatUptime(millis()) + "\"" +
                  ",\"free_heap\":" + String(ESP.getFreeHeap()) +
                  ",\"sync_done\":" + (syncDone ? "true" : "false") +
                  ",\"sync_failed\":" + (syncFailed ? "true" : "false") +
                  ",\"sync_in_progress\":" + String(syncInProgress ? "true" : "false") +
                  ",\"busy\":" + String((recordingInProgress || syncInProgress) ? "true" : "false") +
                  ",\"sync_msg\":\"" + syncMsg + "\"" +
                  ",\"sd_has_file\":" + (sdHasFile() ? "true" : "false") +
                  ",\"sd_ready\":" + String(sdReady ? "true" : "false") +
                  ",\"mic_ready\":" + String(micReady ? "true" : "false") +
                  ",\"recording\":" + String(recordingInProgress ? "true" : "false") +
                  ",\"last_mic_file\":\"" + lastMicWavFile + "\"" +
                  ",\"sd_total\":" + String(sdReady ? (uint32_t)(SD.totalBytes() / 1024) : 0) + " KB" +
                  ",\"sd_used\":" + String(sdReady ? (uint32_t)(SD.usedBytes() / 1024) : 0) + " KB" +
                  ",\"ram_ready\":" + (ramReady ? "true" : "false") +
                  ",\"ram_size\":" + String(ramSize) +
                  ",\"node_enabled\":" + (nodeEnabled ? "true" : "false") +
                  ",\"builtin_wav_size\":" + String(TEST_WAV_SIZE) +
                  ",\"battery_voltage\":" + String(_bv, 2) +
                  ",\"battery_voltage_raw\":" + String(_br) +
                  ",\"battery_percent\":" + String(_bp) + "}");
}

// GET /battery — đọc điện áp pin + % còn lại
void handleBattery()
{
  float v = readBatteryVoltage();
  int p = batteryPercent(v);
  int r = readBatteryRaw();
  String j = "{\"voltage\":" + String(v, 2) +
             ",\"voltage_raw\":" + String(r) +
             ",\"percent\":" + String(p) +
             ",\"v_min\":" + String(BATTERY_V_MIN, 1) +
             ",\"v_max\":" + String(BATTERY_V_MAX, 1) +
             ",\"adc_pin\":" + String(BATTERY_ADC_PIN) +
             ",\"divider\":" + String(BATTERY_DIVIDER, 1) + "}";
  server.send(200, "application/json", j);
}

void handleFileInfo()
{
  bool has = sdHasFile();
  size_t sz = sdFileSize();
  String j = "{\"has_file\":" + String(has ? "true" : "false");
  j += ",\"path\":\"" + String(AUDIO_WAV_PATH) + "\"";
  j += ",\"size\":" + String(sz);
  j += ",\"size_kb\":" + String(sz / 1024.0f, 1);
  if (has && ramReady && ramBuf)
    j += ",\"wav_info\":" + wavInfoJson(ramBuf, ramSize);
  j += ",\"sync_done\":\"" + String(syncDone ? "true" : "false") + "\"";
  j += ",\"sync_msg\":\"" + syncMsg + "\"";
  j += ",\"free_heap\":" + String(ESP.getFreeHeap()) + "}";
  server.send(200, "application/json", j);
}

// GET /file/list
void handleFileList()
{
  if (!sdReady)
  {
    server.send(500, "application/json", "{\"error\":\"sd not ready\"}");
    return;
  }

  String j = "{\"files\":[";
  int count = 0;

  auto appendFileJson = [&](const String &path, const String &displayName)
  {
    File f2 = SD.open(path, "r");
    if (!f2 || f2.isDirectory())
    {
      if (f2)
        f2.close();
      return;
    }

    size_t sz = f2.size();
    String extChk = displayName;
    extChk.toLowerCase();
    bool isWav = extChk.endsWith(".wav");
    float dur = 0.0f;

    if (isWav && sz >= 44)
    {
      uint8_t hdr[44];
      f2.seek(0);
      if (f2.read(hdr, 44) == 44)
      {
        uint16_t ch = hdr[22] | (hdr[23] << 8);
        uint32_t sr = hdr[24] | (hdr[25] << 8) | (hdr[26] << 16) | (hdr[27] << 24);
        uint16_t bps = hdr[34] | (hdr[35] << 8);
        uint32_t dsz = hdr[40] | (hdr[41] << 8) | (hdr[42] << 16) | (hdr[43] << 24);
        if (sr > 0 && ch > 0 && bps > 0)
          dur = (float)dsz / (sr * ch * (bps / 8));
      }
    }
    f2.close();

    int di = displayName.lastIndexOf('.');
    String extStr = (di >= 0) ? displayName.substring(di) : "";
    extStr.toLowerCase();
    String mime = mimeForExt(extStr);

    if (count > 0)
      j += ",";

    char sz_kb[16];
    snprintf(sz_kb, sizeof(sz_kb), "%.1f KB", sz / 1024.0f);

    j += "{\"name\":\"" + displayName + "\"";
    j += ",\"path\":\"" + path + "\"";
    j += ",\"size\":" + String(sz);
    j += ",\"size_kb\":\"" + String(sz_kb) + "\"";
    j += ",\"mime\":\"" + mime + "\"";
    if (isWav)
      j += ",\"duration_sec\":" + String(dur, 2);
    j += "}";
    count++;
  };

  File root = SD.open("/");
  if (root)
  {
    File fi = root.openNextFile();
    while (fi)
    {
      if (!fi.isDirectory())
      {
        String path = String(fi.name());
        if (!path.startsWith("/"))
          path = "/" + path;
        String displayName = path.startsWith("/") ? path.substring(1) : path;
        fi.close();
        appendFileJson(path, displayName);
      }
      else
      {
        fi.close();
      }
      fi = root.openNextFile();
    }
    root.close();
  }

  File recRoot = SD.open("/rec");
  if (recRoot && recRoot.isDirectory())
  {
    File rf = recRoot.openNextFile();
    while (rf)
    {
      if (!rf.isDirectory())
      {
        String raw = String(rf.name());
        String base = raw;
        int slash = base.lastIndexOf('/');
        if (slash >= 0)
          base = base.substring(slash + 1);
        String path = "/rec/" + base;
        String displayName = "rec/" + base;
        rf.close();
        appendFileJson(path, displayName);
      }
      else
      {
        rf.close();
      }
      rf = recRoot.openNextFile();
    }
    recRoot.close();
  }

  j += "],\"count\":" + String(count);
  j += ",\"sd_total\":" + String(sdReady ? (uint32_t)(SD.totalBytes() / 1024) : 0);
  j += ",\"sd_used\":" + String(sdReady ? (uint32_t)(SD.usedBytes() / 1024) : 0) + "}";
  server.send(200, "application/json", j);
}

void handleFileDownload()
{
  String name = server.arg("name");
  name.trim();

  String filePath, dlName;
  if (name.length() == 0)
  {
    filePath = AUDIO_WAV_PATH;
    dlName = "audio.wav";
  }
  else
  {
    String pathRaw = name.startsWith("/") ? name : ("/" + name);
    if (SD.exists(pathRaw))
    {
      filePath = pathRaw;
      dlName = name.startsWith("/") ? name.substring(1) : name;
    }
    else
    {
      String baseOnly = name;
      int slash = baseOnly.lastIndexOf('/');
      if (slash >= 0)
        baseOnly = baseOnly.substring(slash + 1);

      String recPath = "/rec/" + baseOnly;
      if (SD.exists(recPath))
      {
        filePath = recPath;
        dlName = "rec/" + baseOnly;
      }
      else
      {
        String safe = sanitizeFilename(baseOnly);
        if (safe.length() == 0)
        {
          server.send(400, "application/json", "{\"error\":\"invalid filename\"}");
          return;
        }

        String rootSafe = "/" + safe;
        String recSafe = "/rec/" + safe;
        if (SD.exists(rootSafe))
        {
          filePath = rootSafe;
          dlName = safe;
        }
        else if (SD.exists(recSafe))
        {
          filePath = recSafe;
          dlName = "rec/" + safe;
        }
        else
        {
          filePath = rootSafe;
          dlName = safe;
        }
      }
    }
  }

  int di = dlName.lastIndexOf('.');
  String ext = (di >= 0) ? dlName.substring(di) : "";
  ext.toLowerCase();
  String mime = mimeForExt(ext);

  if (SD.exists(filePath))
  {
    File f = SD.open(filePath, "r");
    if (f)
    {
      size_t sz = f.size();
      WiFiClient cli = server.client();
      cli.printf("HTTP/1.1 200 OK\r\n"
                 "Content-Type: %s\r\n"
                 "Content-Length: %d\r\n"
                 "Content-Disposition: attachment; filename=\"%s\"\r\n"
                 "Connection: close\r\n\r\n",
                 mime.c_str(), sz, dlName.c_str());
      uint8_t buf[1024];
      size_t sent = 0;
      while (sent < sz && cli.connected())
      {
        size_t rd = f.read(buf, min((size_t)1024, sz - sent));
        if (rd == 0)
          break;
        cli.write(buf, rd);
        sent += rd;
      }
      cli.flush();
      f.close();
      Serial.printf("[Download] '%s' %d/%d bytes MIME=%s\n",
                    dlName.c_str(), sent, sz, mime.c_str());
      blinkLED(3, 100);
      return;
    }
  }

  if (name.length() == 0 || dlName == "audio.wav")
  {
    const uint8_t *src = nullptr;
    size_t srcSz = 0;
    if (ramReady && ramSize > 0)
    {
      src = ramBuf;
      srcSz = ramSize;
    }
    else if (TEST_WAV_SIZE > 0)
    {
      src = TEST_WAV_DATA;
      srcSz = TEST_WAV_SIZE;
    }
    if (src && srcSz > 0)
    {
      WiFiClient cli = server.client();
      cli.printf("HTTP/1.1 200 OK\r\n"
                 "Content-Type: audio/wav\r\n"
                 "Content-Length: %d\r\n"
                 "Content-Disposition: attachment; filename=\"audio.wav\"\r\n"
                 "Connection: close\r\n\r\n",
                 srcSz);
      size_t sent = 0;
      while (sent < srcSz && cli.connected())
      {
        size_t ch = min((size_t)1024, srcSz - sent);
        cli.write(src + sent, ch);
        sent += ch;
      }
      cli.flush();
      blinkLED(3, 100);
      return;
    }
  }

  server.send(404, "application/json", "{\"error\":\"file not found\",\"name\":\"" + dlName + "\"}");
}

void handleFileUpload()
{
  String xFilename = server.header("X-Filename");
  if (xFilename.length() == 0)
    xFilename = server.arg("name");
  xFilename.trim();

  String saveAs = sanitizeFilename(xFilename);
  if (saveAs.length() == 0)
    saveAs = genAutoFilename();

  int clen = 0;
  String clHeader = server.header("Content-Length");
  if (clHeader.length() > 0)
    clen = clHeader.toInt();

  if (clen <= 0)
  {
    server.send(400, "application/json", "{\"error\":\"missing Content-Length\"}");
    return;
  }
  if (clen > (int)MAX_FILE_SIZE)
  {
    server.send(413, "application/json", "{\"error\":\"file too large\"}");
    return;
  }

  // Mở file SD để ghi stream
  String path = "/" + saveAs;
  if (SD.exists(path))
    SD.remove(path);
  File sdFile = SD.open(path, "w");
  if (!sdFile)
  {
    server.send(500, "application/json", "{\"error\":\"sd open failed\"}");
    return;
  }

  // Đọc từ raw TCP client và ghi thang vao SD (không cần buffer toàn bộ trong RAM)
  WiFiClient cli = server.client();
  size_t rx = 0;
  uint8_t chunk[512];
  unsigned long t = millis();
  while (rx < (size_t)clen && cli.connected() && (millis() - t) < 30000)
  {
    size_t av = cli.available();
    if (av > 0)
    {
      size_t want = min(av, min((size_t)512, (size_t)(clen - rx)));
      size_t rd = cli.readBytes(chunk, want);
      if (rd > 0)
      {
        sdFile.write(chunk, rd);
        rx += rd;
        t = millis();
      }
    }
    else
    {
      delay(1);
    }
  }
  sdFile.close();

  Serial.printf("[Upload] '%s' rx=%d/%d bytes\n", saveAs.c_str(), rx, clen);

  bool saved = (rx > 0 && SD.exists(path));
  // Verify kích thước
  if (saved)
  {
    File chk = SD.open(path, "r");
    if (chk)
    {
      saved = (chk.size() == rx);
      chk.close();
    }
  }
  if (!saved)
    SD.remove(path);

  // Nếu là audio.wav → load vào RAM
  if (saved && saveAs == "audio.wav")
  {
    if (ramBuf)
    {
      free(ramBuf);
      ramBuf = nullptr;
      ramSize = 0;
      ramReady = false;
    }
    File f = SD.open(path, "r");
    if (f)
    {
      size_t sz = f.size();
      if (sz <= 2000000)
      {
        ramBuf = (uint8_t *)malloc(sz);
        if (ramBuf)
        {
          size_t rd = f.read(ramBuf, sz);
          f.close();
          ramSize = rd;
          ramReady = (rd >= 44);
        }
        else
          f.close();
      }
      else
        f.close();
    }
  }

  Serial.printf("[Upload] '%s' %d bytes → %s\n", saveAs.c_str(), rx, saved ? "OK" : "FAIL");
  blinkLED(saved ? 5 : 2, 80);

  String resp = "{\"status\":\"" + String(saved ? "ok" : "fail") + "\""
                                                                   ",\"filename\":\"" +
                saveAs + "\""
                         ",\"size\":" +
                String(rx) +
                ",\"sd_saved\":" + String(saved ? "true" : "false") + "}";
  server.send(saved ? 200 : 500, "application/json", resp);
}

void handleFileClear()
{
  bool ok = SD.remove(AUDIO_WAV_PATH);
  if (ramBuf)
  {
    free(ramBuf);
    ramBuf = nullptr;
    ramSize = 0;
    ramReady = false;
  }
  syncDone = false;
  syncMsg = "cleared";
  server.send(200, "application/json",
              ok ? "{\"status\":\"ok\",\"message\":\"File SD da xoa\"}" : "{\"status\":\"ok\",\"message\":\"Khong co file SD de xoa\"}");
}

// POST /file/delete?name=<filename>
// Thử path gốc trước (không sanitize), fallback sang sanitized name
void handleFileDelete()
{
  String name = server.arg("name");
  name.trim();
  if (name.length() == 0)
  {
    server.send(400, "application/json", "{\"error\":\"missing name\"}");
    return;
  }

  // Thử path trực tiếp trước
  String pathRaw = name.startsWith("/") ? name : ("/" + name);
  String path = "";
  if (SD.exists(pathRaw))
  {
    path = pathRaw;
  }
  else
  {
    String safe = sanitizeFilename(name);
    if (safe.length() > 0)
    {
      String pathSafe = "/" + safe;
      if (SD.exists(pathSafe))
        path = pathSafe;
    }
  }

  if (path.length() == 0)
  {
    Serial.printf("[SD] Delete '%s' - not found\n", name.c_str());
    server.send(404, "application/json", "{\"error\":\"file not found\"}");
    return;
  }

  bool ok = SD.remove(path);
  if (ok && path == String(AUDIO_WAV_PATH))
  {
    if (ramBuf)
    {
      free(ramBuf);
      ramBuf = nullptr;
      ramSize = 0;
      ramReady = false;
    }
    syncDone = false;
    syncMsg = "deleted";
  }
  server.send(ok ? 200 : 500, "application/json",
              ok ? "{\"status\":\"ok\"}" : "{\"error\":\"delete failed\"}");
  Serial.printf("[SD] Delete '%s' → %s\n", path.c_str(), ok ? "OK" : "FAIL");
}

void handleRamInfo()
{
  if (!ramReady || ramSize < 44)
  {
    server.send(200, "application/json",
                String("{\"ram_ready\":false,\"free_heap\":") + String(ESP.getFreeHeap()) +
                    ",\"sd_has_file\":" + String(sdHasFile() ? "true" : "false") +
                    ",\"sync_msg\":\"" + syncMsg + "\"}");
    return;
  }
  char magic[5] = {0};
  memcpy(magic, ramBuf, 4);
  String j = "{\"ram_ready\":true,\"size_bytes\":" + String(ramSize);
  j += ",\"magic\":\"" + String(magic) + "\"";
  j += ",\"wav_info\":" + wavInfoJson(ramBuf, ramSize);
  j += ",\"sync_msg\":\"" + syncMsg + "\"";
  j += ",\"free_heap\":" + String(ESP.getFreeHeap()) + "}";
  server.send(200, "application/json", j);
}

// POST /sync
void handleSync()
{
  if (recordingInProgress || syncInProgress)
  {
    server.send(423, "application/json",
                "{\"status\":\"busy\",\"message\":\"Phantom-2 is recording or syncing\"}");
    return;
  }

  server.send(200, "application/json",
              "{\"status\":\"ok\",\"message\":\"Sync Phantom-1 starting\"}");
  syncDone = syncFromNode1();
  syncFailed = !syncDone;
}

// ── Raw TCP Upload Server (port 8081) ─────────────────────────
void handleRawUpload(WiFiClient &cli)
{
  String reqLine = cli.readStringUntil('\n');
  reqLine.trim();
  Serial.printf("[Upload8081] %s\n", reqLine.substring(0, 60).c_str());

  String xFilename = "";
  int clen = 0;
  unsigned long th = millis();
  while (cli.connected() && (millis() - th) < 5000)
  {
    if (!cli.available())
    {
      delay(2);
      continue;
    }
    String line = cli.readStringUntil('\n');
    line.trim();
    if (line.length() == 0)
      break;
    String lo = line;
    lo.toLowerCase();
    if (lo.startsWith("content-length:"))
    {
      String val = line.substring(line.indexOf(':') + 1);
      val.trim();
      clen = val.toInt();
    }
    if (lo.startsWith("x-filename:"))
    {
      xFilename = line.substring(line.indexOf(':') + 1);
      xFilename.trim();
    }
    th = millis();
  }
  Serial.printf("[Upload8081] fname='%s' CL=%d\n", xFilename.c_str(), clen);

  if (clen <= 0 || clen > (int)MAX_FILE_SIZE)
  {
    cli.print("HTTP/1.0 400 Bad Request\r\nContent-Length: 20\r\nConnection: close\r\n\r\n{\"error\":\"bad clen\"}");
    cli.flush();
    return;
  }

  String saveAs = sanitizeFilename(xFilename);
  if (saveAs.length() == 0)
    saveAs = genAutoFilename();
  String path = "/" + saveAs;

  if (SD.exists(path))
    SD.remove(path);
  File sdFile = SD.open(path, "w");
  if (!sdFile)
  {
    cli.print("HTTP/1.0 500 Internal Server Error\r\nContent-Length: 27\r\nConnection: close\r\n\r\n{\"error\":\"sd open failed\"}");
    cli.flush();
    return;
  }

  size_t rx = 0;
  uint8_t chunk[512];
  unsigned long t = millis();
  while (rx < (size_t)clen && cli.connected() && (millis() - t) < 30000)
  {
    size_t av = cli.available();
    if (av > 0)
    {
      size_t want = min(av, min((size_t)512, (size_t)(clen - rx)));
      size_t rd = cli.readBytes(chunk, want);
      if (rd > 0)
      {
        sdFile.write(chunk, rd);
        rx += rd;
        t = millis();
      }
    }
    else
    {
      delay(2);
    }
  }
  sdFile.close();
  Serial.printf("[Upload8081] '%s' rx=%d/%d\n", saveAs.c_str(), rx, clen);

  bool saved = (rx > 0 && SD.exists(path));
  if (saved)
  {
    File chk = SD.open(path, "r");
    if (chk)
    {
      saved = (chk.size() == rx);
      chk.close();
    }
  }
  if (!saved)
    SD.remove(path);

  if (saved && saveAs == "audio.wav")
  {
    if (ramBuf)
    {
      free(ramBuf);
      ramBuf = nullptr;
      ramSize = 0;
      ramReady = false;
    }
    File f = SD.open(path, "r");
    if (f)
    {
      size_t sz = f.size();
      ramBuf = (uint8_t *)malloc(sz);
      if (ramBuf)
      {
        size_t rd = f.read(ramBuf, sz);
        f.close();
        ramSize = rd;
        ramReady = (rd >= 44);
      }
      else
        f.close();
    }
  }

  blinkLED(saved ? 5 : 2, 80);
  Serial.printf("[Upload8081] '%s' %d bytes → %s\n", saveAs.c_str(), rx, saved ? "OK" : "FAIL");

  String resp = "{\"status\":\"" + String(saved ? "ok" : "fail") + "\""
                                                                   ",\"filename\":\"" +
                saveAs + "\""
                         ",\"size\":" +
                String(rx) +
                ",\"spiffs_saved\":" + String(saved ? "true" : "false") + "}";
  cli.printf("HTTP/1.0 %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s",
             saved ? "200 OK" : "500 Internal Server Error",
             (int)resp.length(), resp.c_str());
  cli.flush();
}

void handleNotFound()
{
  server.send(404, "application/json", "{\"error\":\"not found\"}");
}

// ── Raw TCP port 8080 ─────────────────────────────────────────
void handleRawTCP(WiFiClient &client)
{
  String req = client.readStringUntil('\n');
  req.trim();
  int clen = 0;
  String xFilename = "";
  while (client.connected())
  {
    String line = client.readStringUntil('\n');
    line.trim();
    if (line.length() == 0)
      break;
    String lo = line;
    lo.toLowerCase();
    if (lo.startsWith("content-length:"))
      clen = line.substring(line.indexOf(':') + 1).toInt();
    if (lo.startsWith("x-filename:"))
    {
      xFilename = line.substring(line.indexOf(':') + 1);
      xFilename.trim();
    }
  }

  if (req.startsWith("GET"))
  {
    if (sdHasFile())
    {
      File f = SD.open(AUDIO_WAV_PATH, "r");
      if (f)
      {
        size_t sz = f.size();
        client.printf("HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\nContent-Length: %d\r\n"
                      "Content-Disposition: attachment; filename=\"audio.wav\"\r\nConnection: close\r\n\r\n",
                      (int)sz);
        uint8_t buf[1024];
        size_t sent = 0;
        while (sent < sz && client.connected())
        {
          size_t rd = f.read(buf, min((size_t)1024, sz - sent));
          client.write(buf, rd);
          sent += rd;
        }
        f.close();
        client.flush();
        blinkLED(3, 100);
        return;
      }
    }
    const uint8_t *buf = (ramReady && ramSize > 0) ? ramBuf : TEST_WAV_DATA;
    size_t sz = (ramReady && ramSize > 0) ? ramSize : TEST_WAV_SIZE;
    if (sz == 0)
    {
      client.print("HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n");
      return;
    }
    client.printf("HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\nContent-Length: %d\r\n"
                  "Content-Disposition: attachment; filename=\"audio.wav\"\r\nConnection: close\r\n\r\n",
                  sz);
    size_t sent = 0;
    while (sent < sz && client.connected())
    {
      size_t ch = min((size_t)1024, sz - sent);
      client.write(buf + sent, ch);
      sent += ch;
    }
    client.flush();
    blinkLED(3, 100);
  }
  else if (req.startsWith("POST"))
  {
    if (clen <= 0 || clen > (int)MAX_FILE_SIZE)
    {
      client.print("HTTP/1.1 400\r\nConnection: close\r\n\r\n");
      return;
    }
    uint8_t *buf = (uint8_t *)malloc(clen);
    if (!buf)
    {
      client.print("HTTP/1.1 507\r\nConnection: close\r\n\r\n");
      return;
    }
    size_t rx = 0;
    unsigned long t = millis();
    while (rx < (size_t)clen && client.connected() && (millis() - t) < 20000)
    {
      size_t av = client.available();
      if (av > 0)
      {
        size_t ch = min(av, (size_t)(clen - rx));
        client.readBytes(buf + rx, ch);
        rx += ch;
        t = millis();
      }
      else
        delay(1);
    }
    if (rx > 0)
    {
      String saveAs = sanitizeFilename(xFilename);
      if (saveAs.length() == 0)
        saveAs = genAutoFilename();
      bool sv = sdSaveAs(buf, rx, "/" + saveAs);
      if (sv && saveAs == "audio.wav")
      {
        if (ramBuf)
        {
          free(ramBuf);
          ramBuf = nullptr;
          ramSize = 0;
        }
        ramBuf = buf;
        ramSize = rx;
        ramReady = true;
        buf = nullptr;
      }
      if (buf)
        free(buf);

      String r = "{\"status\":\"ok\",\"received\":" + String(rx) +
                 ",\"filename\":\"" + saveAs + "\"" +
                 ",\"sd_saved\":" + String(sv ? "true" : "false") + "}";

      client.printf("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s",
                    (int)r.length(), r.c_str());
      blinkLED(5, 80);
    }
    else
    {
      free(buf);
      client.print("HTTP/1.1 400\r\nConnection: close\r\n\r\n{\"error\":\"incomplete\"}");
    }
  }
}

// ── Khai báo trước setup() ────────────────────────────────────
// đồng bộ mỗi 15 giây

// ── Đếm số file SD ───────────────────────────────────────────
int countSdFiles()
{
  if (!sdReady)
    return 0;
  int n = 0;
  File root = SD.open("/");
  File fi = root.openNextFile();
  while (fi)
  {
    if (!fi.isDirectory())
      n++;
    fi.close();
    fi = root.openNextFile();
  }
  root.close();
  return n;
}

// ── Setup ─────────────────────────────────────────────────────
void setup()
{
  Serial.begin(115200);
  delay(500);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  // Da bo chuc nang BOOT button va flash-safe mode.
  // Node khoi dong la chay luon.

  // ── Battery ADC (GPIO34 = ADC1_CH6, an toàn với WiFi) ──
  analogReadResolution(12);
  analogSetPinAttenuation(BATTERY_ADC_PIN, ADC_11db);

  Serial.println("\n══════════════════════════════");
  Serial.println(" ESP32 NODE-2  (APSTA + SD)");
  Serial.println("══════════════════════════════");

  sdReady = setupSDCard();

  WiFi.mode(WIFI_AP_STA);
  IPAddress apIP(192, 168, 5, 1);
  IPAddress gw(192, 168, 5, 1);
  IPAddress sn(255, 255, 255, 0);
  WiFi.softAPConfig(apIP, gw, sn);
  WiFi.softAP(MY_AP_SSID, MY_AP_PASSWORD, MY_AP_CHANNEL, MY_AP_HIDDEN, MY_AP_MAX_CON);
  delay(200);
  Serial.printf("[AP] SSID: %s  IP: %s\n", MY_AP_SSID, WiFi.softAPIP().toString().c_str());
  digitalWrite(LED_PIN, HIGH);

  // Collect headers cho /file/upload
  const char *collectHeaders[] = {"X-Filename", "Content-Length", "Content-Type"};
  server.collectHeaders(collectHeaders, 3);

  server.on("/status", HTTP_GET, handleStatus);
  server.on("/file/info", HTTP_GET, handleFileInfo);
  server.on("/file/list", HTTP_GET, handleFileList);
  server.on("/file/download", HTTP_GET, handleFileDownload);
  server.on("/file/upload", HTTP_POST, handleFileUpload);
  server.on("/file/clear", HTTP_POST, handleFileClear);
  server.on("/file/delete", HTTP_POST, handleFileDelete);
  server.on("/ram/info", HTTP_GET, handleRamInfo);
  server.on("/battery", HTTP_GET, handleBattery);
  server.on("/sync", HTTP_POST, handleSync);
  server.on("/sync/status", HTTP_GET, handleSyncStatus);
  server.on("/busy", HTTP_GET, handleBusy);
  server.on("/sd/list", HTTP_GET, handleSdList);
  server.on("/sd/download", HTTP_GET, handleSdDownload);

  server.on("/audio/info", HTTP_GET, handleFileInfo);
  server.on("/ram/clear", HTTP_POST, handleFileClear);
  server.onNotFound(handleNotFound);
  server.begin();
  audioServer.begin();
  uploadServer.begin();

  setupI2SMic();

  // Lay gio Viet Nam (GMT+7) qua NTP.
  // Neu Node-2 khong co internet, ten file se fallback thanh rec_no_time_xxxxxx.wav.
  configTime(7 * 3600, 0, "pool.ntp.org", "time.nist.gov");

  int fc = countSdFiles();
  Serial.printf("[SD] Danh sach hien co: %d file. Khong sync khi khoi dong.\n", fc);
  syncDone = false;
  syncFailed = false;
  syncMsg = "ready: auto sync every 60s + sync after each recording";

  Serial.println("\n[Sẵn sàng] Node-2 (Thiết bị B) — Endpoints:");
  Serial.printf("  Kết nối WiFi: %s / %s\n", MY_AP_SSID, MY_AP_PASSWORD);
  Serial.printf("  GET  http://%s/status\n", MY_AP_IP_STR);
  Serial.printf("  GET  http://%s/file/list\n", MY_AP_IP_STR);
  Serial.printf("  GET  http://%s/file/download?name=photo.png\n", MY_AP_IP_STR);
  Serial.printf("  POST http://%s/file/upload  (X-Filename: myfile.txt)\n", MY_AP_IP_STR);
  Serial.printf("  POST http://%s/sync\n", MY_AP_IP_STR);
  Serial.printf("  GET  http://%s:8080/  (TCP WAV)\n", MY_AP_IP_STR);
  Serial.println("  Auto record: sound trigger -> record 30s -> sync Phantom-1 once");
  Serial.println("  BOOT button toggle: DISABLED");
}

// ── Loop ──────────────────────────────────────────────────────
void loop()
{
  server.handleClient();
  handleAutoMicRecord();
  handleAutoSync();

  if (!micArmed && !recordingInProgress)
  {
    MicVoiceStats st = readMicVoiceStats();
    if (st.rms15 < MIC_REARM_RMS_LEVEL)
      micArmed = true;
  }

  WiFiClient c = audioServer.accept();
  if (c)
  {
    unsigned long t = millis();
    while (!c.available() && c.connected() && (millis() - t) < 3000)
      delay(1);
    if (c.available())
      handleRawTCP(c);
    c.stop();
  }

  // Raw TCP Upload Server port 8081 — bypass WebServer body issue
  WiFiClient uc = uploadServer.accept();
  if (uc)
  {
    unsigned long t = millis();
    while (!uc.available() && uc.connected() && (millis() - t) < 5000)
      delay(2);
    if (uc.available())
      handleRawUpload(uc);
    uc.stop();
  }

  // Auto-sync moi 60 giay. Neu Phantom-2 dang ghi am hoac peer busy thi bo qua lan do.
}
