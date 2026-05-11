/*
 * Phantom Dual Equal Firmware — APSTA + SD File Relay + MIC Recorder + AES-GCM .bin Sync
 * Dung chung cho ca Phantom-1 va Phantom-2.
 *
 * Cach dung:
 *   - File Phantom_1_equal.ino: #define NODE_ID 1
 *   - File Phantom_2_equal.ino: #define NODE_ID 2
 *
 * Hai node co cung vai tro:
 *   - Moi node phat WiFi AP rieng de laptop truy cap.
 *   - Moi node co MIC, tu dong ghi am vao /rec.
 *   - WAV chi la file tam trong luc ghi; ghi xong ma hoa AES-GCM thanh .bin.
 *   - .bin la file chinh thuc de luu va dong bo hai chieu.
 *   - Khi mot node co file moi, node do yeu cau peer POST /sync de peer keo file ve.
 *   - Neu peer dang ghi, peer se dung ghi an toan, finalize, ma hoa .bin roi sync.
 *
 * Endpoints port 80:
 *   GET  /status
 *   GET  /file/info
 *   GET  /file/list
 *   GET  /file/download?name=<filename>
 *   POST /file/upload
 *   POST /file/clear
 *   POST /file/delete?name=<filename>
 *   GET  /ram/info
 *   POST /sync
 *   GET  /sync/status
 *   GET  /sd/list
 *   GET  /sd/download?name=<filename>
 *   GET  /battery
 *
 * Port 8080: Raw TCP GET/POST audio.wav / file upload cu
 * Port 8081: Raw TCP upload file bat ky dinh dang
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <SD.h>
#include <SPI.h>
#include <FS.h>
#include <driver/i2s.h>
#include <time.h>
#include <vector>
#include <mbedtls/gcm.h>
#include <esp_system.h>
#include "test_wav.h"

// ── Cau hinh node doi xung ───────────────────────────────────
#define NODE_ID 2

#if NODE_ID == 1
#define MY_AP_SSID "Phantom-1"
#define MY_AP_CHANNEL 1
#define MY_AP_IP_STR "192.168.4.1"
#define PEER_SSID "Phantom-2"
#define PEER_IP "192.168.5.1"
#elif NODE_ID == 2
#define MY_AP_SSID "Phantom-2"
#define MY_AP_CHANNEL 6
#define MY_AP_IP_STR "192.168.5.1"
#define PEER_SSID "Phantom-1"
#define PEER_IP "192.168.4.1"
#else
#error "NODE_ID must be 1 or 2"
#endif

#if NODE_ID == 2
#define SYNC_INITIATOR 1
#else
#define SYNC_INITIATOR 0
#endif

#define MY_AP_PASSWORD "12345678"
#define MY_AP_HIDDEN false
#define MY_AP_MAX_CON 4

#define PEER_PASSWORD "12345678"
#define PEER_TCP_PORT 8080
#define PEER_HTTP_PORT 80

#define LED_PIN 2
#define HTTP_PORT 80
#define AUDIO_PORT 8080
#define UPLOAD_PORT 8081

#define AUDIO_WAV_PATH "/audio.wav"
#define MAX_FILE_SIZE 50000000
#define AUTO_SYNC_INTERVAL_MS 60000UL // disabled: no 60s auto sync; priority sync only
#define PRIORITY_SYNC_WAIT_PEER_IDLE_MS 30000UL

// ── I2S MIC ───────────────────────────────────────────────────
#define I2S_WS 25
#define I2S_SD 22
#define I2S_SCK 26
#define I2S_PORT I2S_NUM_0

#define MIC_SAMPLE_RATE 16000
#define MIC_SAMPLE_BITS 16
#define MIC_I2S_READ_LEN 1024

// ── Auto trigger record ───────────────────────────────────────
#define AUTO_RECORD_MS 30000UL

// ── Human voice trigger tuning ────────────────────────────────
// FIX nhẹ CPU: Trigger ghi âm CHỈ theo peak14.
// Không tính rms15/zcr trong chế độ chờ vì không dùng để quyết định ghi.
// Cần peak14 vượt ngưỡng đủ 8 frame liên tiếp mới bắt đầu ghi.

// Nguong peak14 dung chung cho hai node de vai tro va logic giong nhau.
// Neu moi truong qua on, tang len 4000-6000; neu kho bat tieng, ha xuong 1500-2500.
#define MIC_TRIGGER_PEAK14_LEVEL 10000

// Các define cũ giữ lại để tránh lỗi tham chiếu, nhưng KHÔNG dùng để trigger nữa.
// Các define cũ đã bỏ khỏi logic trigger để tiết kiệm CPU.
#define MIC_VOICE_FRAMES_TO_TRIGGER 8
#define MIC_COOLDOWN_MS 3000UL
#define MIC_LOG_INTERVAL_MS 1000UL // giảm log idle: 0.5s -> 3s
#define REC_LOG_INTERVAL_MS 2000UL // giảm log khi đang ghi: 1s -> 5s

// Keep legacy defines to avoid accidental references breaking build
#define MIC_TRIGGER_LEVEL 5000
#define MIC_REARM_LEVEL 3000

// ── SD card ───────────────────────────────────────────────────
#define SD_CS 5
#define SD_SCK 18
#define SD_MISO 19
#define SD_MOSI 23

// ── Battery ADC ───────────────────────────────────────────────
// GPIO34 = ADC1_CH6. Neu mach cua ban khong co do pin, co the bo qua endpoint /battery.
#define BATTERY_ADC_PIN 34
#define BATTERY_DIVIDER 2.0f
#define BATTERY_V_MIN 3.0f
#define BATTERY_V_MAX 4.2f

WebServer server(HTTP_PORT);
WiFiServer audioServer(AUDIO_PORT);
WiFiServer uploadServer(UPLOAD_PORT);

// ── RAM buffer cho audio.wav nho ──────────────────────────────
uint8_t *ramBuf = nullptr;
size_t ramSize = 0;
bool ramReady = false;

// ── State ─────────────────────────────────────────────────────
bool nodeEnabled = true;
bool sdReady = false;
bool micReady = false;
bool micArmed = true;
bool recordingInProgress = false;
uint32_t micFileCounter = 0;
String lastMicWavFile = "none";
unsigned long lastMicTriggerMs = 0;
unsigned long lastRecordLoudMs = 0;

bool syncDone = false;
bool syncFailed = false;
String syncMsg = "not started";
bool syncInProgress = false;
bool syncPending = false;               // Co file local moi/cap nhat can dong bo sang Phantom-2
bool peerSyncRequestInProgress = false; // Dang yeu cau Phantom-2 keo file ve
String syncPendingReason = "none";
unsigned long lastAutoSyncMs = 0;
unsigned long lastPrioritySyncAttemptMs = 0;
uint8_t prioritySyncFailCount = 0;
bool lastPriorityFailPeerNotFound = false;

// Sync priority control:
// Khi peer gọi POST /sync trong lúc đang ghi âm, thiết bị sẽ dừng bản ghi hiện tại,
// finalize WAV -> mã hóa .bin, rồi chạy sync ngay sau khi recordingInProgress=false.
bool stopRecordingForSyncRequested = false;
bool peerSyncQueuedByRequest = false;

// HTTP multipart upload state for /file/upload
File httpUploadFile;
String httpUploadFilename = "";
String httpUploadPath = "";
size_t httpUploadSize = 0;
bool httpUploadOk = false;
String httpUploadError = "none";

// ── Function prototypes ───────────────────────────────────────
bool setupSDCard();
int countSdFiles();
String formatUptime(uint32_t ms);
bool syncFromPeer();
void restoreMyAP();
String httpGetFromPeer(const char *path, int timeoutMs);
bool isPeerBusy();
void handleBusy();
void handleAutoSync();
void handleFileUploadDone();
void handleFileUploadStream();
void markLocalFileChanged(const String &reason);
void handlePrioritySync();
bool requestPeerPullSync(bool requestBackSync = true);
void debugScanPeerNetworks(const char *targetSsid);
void setupI2SMic();
String genMicFilename();
struct MicVoiceStats;
MicVoiceStats readMicVoiceStats();
bool recordTriggeredWavToSD(const char *path, uint32_t durationMs = AUTO_RECORD_MS);
void handleAutoMicRecord();
void syncMicCounterFromSd();
bool verifyPeerHasLocalFile(const String &localPath);
bool verifyPeerHasAllLocalRecFiles();
bool verifyBidirectionalRecBinSync();
bool httpDownloadFileFromPeer(const String &filename, WiFiClient *persist);
String encryptedPathFromWavPath(const String &wavPath);
bool aesGcmEncryptFile(const String &plainPath, const String &encPath, bool deletePlainAfterEncrypt);
bool isZeroByteRecFile(const String &path);
void cleanupZeroByteRecFiles();
void cleanupAllZeroByteFilesOnBoot();
uint32_t cleanupZeroByteFilesRecursive(const char *dirPath, uint8_t depth);
String normalizeRecPath(const String &path);

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

// ── SD init ───────────────────────────────────────────────────
bool setupSDCard()
{
  SPI.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);
  if (!SD.begin(SD_CS, SPI))
  {
    Serial.println("[SD] Initialization FAILED!");
    return false;
  }

  if (!SD.exists("/rec"))
  {
    if (SD.mkdir("/rec"))
      Serial.println("[SD] Created /rec folder.");
    else
      Serial.println("[SD] WARNING: Cannot create /rec folder.");
  }

  Serial.println("[SD] Initialization OK.");
  return true;
}

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
  if (ext == ".bin")
    return "application/octet-stream";
  return "application/octet-stream";
}

// ── Sanitize filename ─────────────────────────────────────────
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

// ── SD helpers ────────────────────────────────────────────────
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

String normalizeRecPath(const String &pathIn)
{
  String p = pathIn;
  p.trim();
  if (p.length() == 0)
    return "";

  // SD.open("/rec").openNextFile() trên một số bản core ESP32 có thể trả:
  //   "rec2_xxx.wav", "/rec2_xxx.wav" hoặc "/rec/rec2_xxx.wav".
  // Chuẩn hóa tất cả file ghi âm rec*.wav/rec*.bin về đúng thư mục /rec/.
  String base = p;
  int slash = base.lastIndexOf('/');
  if (slash >= 0)
    base = base.substring(slash + 1);

  String lowBase = base;
  lowBase.toLowerCase();
  bool isRecAudio = ((lowBase.startsWith("rec") &&
                      (lowBase.endsWith(".wav") || lowBase.endsWith(".bin"))) ||
                     (lowBase.startsWith("phantom_") && lowBase.endsWith(".bin")));

  if (isRecAudio)
    return "/rec/" + base;

  if (p.startsWith("/"))
    return p;
  return "/" + p;
}

#define MIN_VALID_ENC_REC_SIZE 34
#define RECOVER_WAV_MIN_SIZE (100UL * 1024UL)

bool isRecWavPath(const String &pathIn)
{
  String p = normalizeRecPath(pathIn);
  String base = p;
  int slash = base.lastIndexOf('/');
  if (slash >= 0)
    base = base.substring(slash + 1);
  base.toLowerCase();
  return base.startsWith("rec") && base.endsWith(".wav");
}

bool isRecEncPath(const String &pathIn)
{
  String p = normalizeRecPath(pathIn);
  String base = p;
  int slash = base.lastIndexOf('/');
  if (slash >= 0)
    base = base.substring(slash + 1);
  base.toLowerCase();
  if (!base.endsWith(".bin"))
    return false;
  return base.startsWith("rec") || base.startsWith("phantom_");
}

bool isValidEncSize(size_t sz)
{
  return sz > MIN_VALID_ENC_REC_SIZE;
}

size_t safeFileSize(const String &path)
{
  if (!sdReady || !SD.exists(path))
    return 0;
  File f = SD.open(path, "r");
  if (!f)
    return 0;
  size_t sz = f.size();
  f.close();
  return sz;
}

void removeIfExists(const String &path)
{
  if (path.length() > 0 && SD.exists(path))
    SD.remove(path);
}

// ── Boot cleanup: xóa tất cả file 0 byte trên SD ─────────────
// Mục tiêu: nếu thiết bị bị mất nguồn/reset khi đang tạo file,
// mọi file 0KB còn sót trên thẻ sẽ bị xóa ngay khi khởi động lại.
uint32_t cleanupZeroByteFilesRecursive(const char *dirPath, uint8_t depth)
{
  if (!sdReady || !dirPath || depth > 8)
    return 0;

  File root = SD.open(dirPath);
  if (!root || !root.isDirectory())
  {
    if (root)
      root.close();
    return 0;
  }

  uint32_t deletedCount = 0;
  File f = root.openNextFile();
  while (f)
  {
    String name = String(f.name());
    bool isDir = f.isDirectory();
    size_t sz = isDir ? 0 : f.size();

    String path = name;
    if (!path.startsWith("/"))
    {
      String parent = String(dirPath);
      if (parent.endsWith("/"))
        path = parent + name;
      else
        path = parent + "/" + name;
    }

    f.close();

    if (isDir)
    {
      deletedCount += cleanupZeroByteFilesRecursive(path.c_str(), depth + 1);
    }
    else if (sz == 0)
    {
      if (SD.remove(path))
      {
        deletedCount++;
        Serial.printf("[BOOT CLEANUP] Delete 0KB file: %s\n", path.c_str());
      }
      else
      {
        Serial.printf("[BOOT CLEANUP] Cannot delete 0KB file: %s\n", path.c_str());
      }
    }

    f = root.openNextFile();
    delay(1);
  }

  root.close();
  return deletedCount;
}

void cleanupAllZeroByteFilesOnBoot()
{
  if (!sdReady)
    return;

  Serial.println("[BOOT CLEANUP] Scan SD for 0KB files...");
  uint32_t deletedCount = cleanupZeroByteFilesRecursive("/", 0);
  Serial.printf("[BOOT CLEANUP] Done. Deleted 0KB files: %lu\n", (unsigned long)deletedCount);
}

// ── Cleanup file ghi âm lỗi: xóa .wav/.bin rỗng để không kẹt sync ──

// ── Cleanup file ghi âm lỗi và chuẩn hóa dữ liệu chính thức ──
// Quy tắc thực tế:
//   - .bin là file chính thức để lưu/sync.
//   - .wav chỉ là file tạm trong lúc ghi/mã hóa.
//   - .wav/.bin rỗng hoặc .bin quá nhỏ đều bị xóa.
//   - .wav còn sót nhưng đã có .bin hợp lệ tương ứng thì xóa .wav.
//   - file rec*.wav/rec*.bin nằm nhầm root / sẽ được chuyển về /rec/ nếu có thể.
bool isZeroByteRecFile(const String &pathIn)
{
  if (!sdReady || pathIn.length() == 0)
    return false;

  String path = normalizeRecPath(pathIn);
  if (!isRecWavPath(path) && !isRecEncPath(path))
    return false;

  return safeFileSize(path) == 0;
}

void cleanupZeroByteRecFiles()
{
  if (!sdReady)
    return;

  if (!SD.exists("/rec"))
    SD.mkdir("/rec");

  auto cleanOne = [](const String &rawIn, size_t sz)
  {
    String raw = rawIn;
    if (!raw.startsWith("/"))
      raw = "/" + raw;

    String norm = normalizeRecPath(raw);
    bool isWav = isRecWavPath(norm);
    bool isEnc = isRecEncPath(norm);

    if (!isWav && !isEnc)
      return;

    // WAV là file tạm trong lúc ghi/mã hóa.
    // Quy tắc:
    // - WAV nhỏ hơn 100KB: coi như dở/lỗi -> xóa.
    // - WAV >= 100KB: thử mã hóa sang .bin trước; nếu thành công thì xóa WAV.
    if (isWav)
    {
      if (sz >= RECOVER_WAV_MIN_SIZE)
      {
        String encPath = encryptedPathFromWavPath(norm);
        Serial.printf("[RECOVER] Found WAV >=100KB, try encrypt: %s -> %s size=%lu\n",
                      norm.c_str(), encPath.c_str(), (unsigned long)sz);

        bool encOk = aesGcmEncryptFile(norm, encPath, true);
        if (encOk)
        {
          Serial.printf("[RECOVER] Encrypt OK: %s\n", encPath.c_str());
          markLocalFileChanged("recover leftover wav " + encPath);
        }
        else
        {
          // Giu file de lan cleanup sau thu lai, tranh mat du lieu.
          Serial.printf("[RECOVER] Encrypt FAILED, keep WAV for retry: %s\n", norm.c_str());
        }
      }
      else
      {
        Serial.printf("[CLEANUP] Delete leftover/incomplete WAV (<100KB): raw=%s norm=%s size=%lu\n",
                      raw.c_str(), norm.c_str(), (unsigned long)sz);

        removeIfExists(raw);
        if (raw != norm)
          removeIfExists(norm);
      }

      return;
    }

    // Nếu file .bin nằm nhầm ở root thì chuyển về /rec/ để mọi API thống nhất đường dẫn.
    if (isEnc && raw != norm && SD.exists(raw) && !SD.exists(norm) && sz > 0)
    {
      Serial.printf("[CLEANUP] Move misplaced BIN: %s -> %s\n",
                    raw.c_str(), norm.c_str());
      SD.rename(raw, norm);
    }

    size_t realSize = safeFileSize(norm);
    if (realSize == 0 && SD.exists(raw))
      realSize = safeFileSize(raw);

    // .bin rỗng hoặc quá nhỏ thì không hợp lệ, xóa để tránh kẹt sync.
    if (isEnc && !isValidEncSize(realSize))
    {
      Serial.printf("[CLEANUP] Delete invalid/zero BIN: %s size=%lu\n",
                    norm.c_str(), (unsigned long)realSize);

      removeIfExists(norm);
      if (raw != norm)
        removeIfExists(raw);

      return;
    }
  };

  auto scanDir = [&](const char *dirPath)
  {
    File root = SD.open(dirPath);
    if (!root || !root.isDirectory())
    {
      if (root)
        root.close();
      return;
    }

    File f = root.openNextFile();
    while (f)
    {
      String raw = String(f.name());
      bool isDir = f.isDirectory();
      size_t sz = isDir ? 0 : f.size();
      f.close();

      if (!isDir)
        cleanOne(raw, sz);

      f = root.openNextFile();
      server.handleClient();
      delay(1);
    }

    root.close();
  };

  // Quét cả root và /rec vì một số core ESP32 có thể trả path lệch.
  scanDir("/");
  scanDir("/rec");
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
    Serial.printf("[SD] SaveAs '%s' %d/%d -> OK\n", path.c_str(), wr, size);
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
  Serial.printf("[SD] Load %d bytes to RAM -> %s\n", rd, ramReady ? "OK" : "FAIL");
  return ramReady;
}

// ── AES-GCM file encryption ───────────────────────────────────
// File format: "PHGCM1" + IV(12 bytes) + ciphertext + TAG(16 bytes)
// IMPORTANT: Change this demo key before real deployment. UI decrypt must use the same 64-hex key.
static const uint8_t AES_GCM_KEY[32] = {
    0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77,
    0x88, 0x99, 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff,
    0x10, 0x32, 0x54, 0x76, 0x98, 0xba, 0xdc, 0xfe,
    0xef, 0xcd, 0xab, 0x89, 0x67, 0x45, 0x23, 0x01};

#define BIN_MAGIC "PHGCM1"
#define BIN_MAGIC_LEN 6
#define AES_GCM_IV_LEN 12
#define AES_GCM_TAG_LEN 16
#define AES_GCM_BUF_LEN 1024

String encryptedPathFromWavPath(const String &wavPath)
{
  String encPath = wavPath;
  int dot = encPath.lastIndexOf('.');
  if (dot >= 0)
    encPath = encPath.substring(0, dot);
  encPath += ".bin";
  return encPath;
}

bool aesGcmEncryptFile(const String &plainPath, const String &encPath, bool deletePlainAfterEncrypt = true)
{
  if (!sdReady || !SD.exists(plainPath))
  {
    Serial.println("[AES-GCM] Plain file not found: " + plainPath);
    return false;
  }

  File in = SD.open(plainPath, "r");
  if (!in)
  {
    Serial.println("[AES-GCM] Cannot open plain file: " + plainPath);
    return false;
  }

  if (SD.exists(encPath))
    SD.remove(encPath);

  File out = SD.open(encPath, FILE_WRITE);
  if (!out)
  {
    in.close();
    Serial.println("[AES-GCM] Cannot create encrypted file: " + encPath);
    return false;
  }

  uint8_t iv[AES_GCM_IV_LEN];
  esp_fill_random(iv, AES_GCM_IV_LEN);

  out.write((const uint8_t *)BIN_MAGIC, BIN_MAGIC_LEN);
  out.write(iv, AES_GCM_IV_LEN);

  mbedtls_gcm_context ctx;
  mbedtls_gcm_init(&ctx);

  int ret = mbedtls_gcm_setkey(&ctx, MBEDTLS_CIPHER_ID_AES, AES_GCM_KEY, 256);
  if (ret == 0)
    ret = mbedtls_gcm_starts(&ctx, MBEDTLS_GCM_ENCRYPT, iv, AES_GCM_IV_LEN, NULL, 0);

  uint8_t inBuf[AES_GCM_BUF_LEN];
  uint8_t outBuf[AES_GCM_BUF_LEN];
  size_t totalIn = 0;

  while (ret == 0 && in.available())
  {
    size_t rd = in.read(inBuf, AES_GCM_BUF_LEN);
    if (rd == 0)
      break;

    ret = mbedtls_gcm_update(&ctx, rd, inBuf, outBuf);
    if (ret != 0)
      break;

    size_t wr = out.write(outBuf, rd);
    if (wr != rd)
    {
      ret = -1;
      break;
    }
    totalIn += rd;
    server.handleClient();
    delay(1);
  }

  uint8_t tag[AES_GCM_TAG_LEN];
  if (ret == 0)
    ret = mbedtls_gcm_finish(&ctx, tag, AES_GCM_TAG_LEN);

  if (ret == 0)
  {
    size_t wr = out.write(tag, AES_GCM_TAG_LEN);
    if (wr != AES_GCM_TAG_LEN)
      ret = -1;
  }

  mbedtls_gcm_free(&ctx);
  in.close();
  out.close();

  if (ret != 0 || totalIn == 0)
  {
    SD.remove(encPath);
    Serial.printf("[AES-GCM] Encrypt FAILED ret=%d plain=%s enc=%s\n", ret, plainPath.c_str(), encPath.c_str());
    return false;
  }

  // Verify file .bin sau khi ghi xong.
  // Format toi thieu = magic 6 + IV 12 + TAG 16 = 34 bytes.
  // Neu <= 34 thi coi nhu file ma hoa hong/rong, xoa luon va khong sync.
  size_t encSize = 0;
  if (SD.exists(encPath))
  {
    File chk = SD.open(encPath, "r");
    if (chk)
    {
      encSize = chk.size();
      chk.close();
    }
  }

  if (encSize <= (BIN_MAGIC_LEN + AES_GCM_IV_LEN + AES_GCM_TAG_LEN))
  {
    SD.remove(encPath);
    Serial.printf("[AES-GCM] Encrypt output invalid/empty -> delete: %s size=%lu\n",
                  encPath.c_str(), (unsigned long)encSize);
    return false;
  }

  // Ma hoa OK thi chi giu .bin, xoa .wav goc.
  if (deletePlainAfterEncrypt && SD.exists(plainPath))
  {
    bool rmOk = SD.remove(plainPath);
    Serial.printf("[AES-GCM] Delete plain WAV after encrypt: %s -> %s\n",
                  plainPath.c_str(), rmOk ? "OK" : "FAIL");
  }

  Serial.printf("[AES-GCM] Encrypt OK: %s -> %s encSize=%lu plaintext=%lu\n",
                plainPath.c_str(), encPath.c_str(),
                (unsigned long)encSize,
                (unsigned long)totalIn);
  return true;
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
  if (!SD.exists("/rec"))
    SD.mkdir("/rec");

  struct tm timeinfo;
  char buf[80];

  // Co gio NTP: /rec/rec1_YYYYMMDD_HHMMSS.wav hoac /rec/rec2_YYYYMMDD_HHMMSS.wav
  if (getLocalTime(&timeinfo))
  {
    char timeBuf[32];
    strftime(timeBuf, sizeof(timeBuf), "%Y%m%d_%H%M%S", &timeinfo);

    uint16_t extra = 0;
    while (true)
    {
      if (extra == 0)
        snprintf(buf, sizeof(buf), "/rec/rec%d_%s.wav", NODE_ID, timeBuf);
      else
        snprintf(buf, sizeof(buf), "/rec/rec%d_%s_%02u.wav", NODE_ID, timeBuf, extra);

      if (!SD.exists(buf))
        return String(buf);

      extra++;
      if (extra > 99)
        break;
    }
  }

  // Chua co gio NTP: /rec/rec1_no_time_000001.wav hoac /rec/rec2_no_time_000001.wav
  while (true)
  {
    micFileCounter++;
    snprintf(buf, sizeof(buf), "/rec/rec%d_no_time_%06lu.wav",
             NODE_ID,
             (unsigned long)micFileCounter);

    if (!SD.exists(buf))
      return String(buf);
  }
}

void syncMicCounterFromSd()
{
  if (!sdReady)
    return;

  if (!SD.exists("/rec"))
    return;

  File root = SD.open("/rec");
  if (!root || !root.isDirectory())
  {
    if (root)
      root.close();
    return;
  }

  char prefix[24];
  snprintf(prefix, sizeof(prefix), "rec%d_no_time_", NODE_ID);
  const size_t prefixLen = strlen(prefix);

  uint32_t maxCounter = micFileCounter;
  File f = root.openNextFile();
  while (f)
  {
    if (!f.isDirectory())
    {
      String name = String(f.name());
      int slash = name.lastIndexOf('/');
      if (slash >= 0)
        name = name.substring(slash + 1);

      if (name.startsWith(prefix))
      {
        int dot = name.lastIndexOf('.');
        String numberPart = (dot > (int)prefixLen) ? name.substring((int)prefixLen, dot) : "";
        if (numberPart.length() > 0)
        {
          bool numeric = true;
          for (int i = 0; i < (int)numberPart.length(); i++)
          {
            if (!isDigit(numberPart[i]))
            {
              numeric = false;
              break;
            }
          }
          if (numeric)
          {
            uint32_t v = (uint32_t)numberPart.toInt();
            if (v > maxCounter)
              maxCounter = v;
          }
        }
      }
    }

    f.close();
    f = root.openNextFile();
  }
  root.close();

  micFileCounter = maxCounter;
  Serial.printf("[MIC] Counter synced from SD: %lu\n", (unsigned long)micFileCounter);
}

struct MicVoiceStats
{
  int peak14;
  bool voiceLike;
};

MicVoiceStats readMicVoiceStats()
{
  MicVoiceStats st = {0, false};

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

  // Tinh DC offset de loai bo lech nen cua mic I2S.
  int64_t sum = 0;
  for (int i = 0; i < sampleCount; i++)
    sum += samples32[i];
  int32_t dc = (int32_t)(sum / sampleCount);

  // Chỉ tính peak14: bỏ rms15/zcr/peak15 để giảm CPU trong chế độ chờ.
  for (int i = 0; i < sampleCount; i++)
  {
    int32_t centered = samples32[i] - dc;

    int32_t s14 = centered >> 14;
    if (s14 > 32767)
      s14 = 32767;
    if (s14 < -32768)
      s14 = -32768;

    int a14 = abs((int)s14);
    if (a14 > st.peak14)
      st.peak14 = a14;
  }

  st.voiceLike = (st.peak14 >= MIC_TRIGGER_PEAK14_LEVEL);
  return st;
}

bool recordTriggeredWavToSD(const char *path, uint32_t durationMs)
{
  if (!sdReady || !micReady || recordingInProgress)
    return false;

  recordingInProgress = true;

  if (!SD.exists("/rec"))
    SD.mkdir("/rec");

  if (SD.exists(path))
    SD.remove(path);

  File audioFile = SD.open(path, FILE_WRITE);
  if (!audioFile)
  {
    recordingInProgress = false;
    Serial.printf("[MIC] Cannot open file for recording: %s\n", path);
    return false;
  }

  uint8_t emptyHeader[44] = {0};
  audioFile.write(emptyHeader, 44);

  i2s_zero_dma_buffer(I2S_PORT);
  delay(50);

  uint8_t i2sData[MIC_I2S_READ_LEN];
  size_t bytesRead = 0;
  uint32_t totalDataBytes = 0;
  uint32_t samplesRecorded = 0;
  uint32_t maxSamplesAllowed = ((MAX_FILE_SIZE - 44UL) / 2UL);

  int recPeak14 = 0;
  unsigned long lastRecLogMs = millis();
  lastRecordLoudMs = millis();

  digitalWrite(LED_PIN, HIGH);
  Serial.printf("[MIC] REC START: %s (stop after %lu ms silence)\n", path, (unsigned long)durationMs);

  while (true)
  {
    server.handleClient();
    if (!nodeEnabled)
      break;

    if (stopRecordingForSyncRequested)
    {
      Serial.println("[MIC] Stop requested by peer sync -> finalize current recording");
      stopRecordingForSyncRequested = false;
      break;
    }

    if (millis() - lastRecordLoudMs >= durationMs)
    {
      Serial.println("[MIC] Silence timeout -> stop recording");
      break;
    }

    if (samplesRecorded >= maxSamplesAllowed)
    {
      Serial.println("[MIC] Max file size reached -> stop recording");
      break;
    }

    if (i2s_read(I2S_PORT, (void *)i2sData, MIC_I2S_READ_LEN, &bytesRead, portMAX_DELAY) != ESP_OK)
    {
      Serial.println("[MIC] i2s_read failed");
      break;
    }

    int32_t *samples32 = (int32_t *)i2sData;
    int sampleCount = bytesRead / 4;

    int framePeak14 = 0;

    // FIX MIC: tinh DC offset tung frame giong Phantom-2 de loai bo lech nen I2S.
    // Neu khong tru DC, mic co the bi peak/rms cao gia du moi truong yen tinh.
    int64_t dcSum = 0;
    for (int k = 0; k < sampleCount; k++)
    {
      dcSum += samples32[k];
    }
    int32_t dc = (sampleCount > 0) ? (int32_t)(dcSum / sampleCount) : 0;

    // 32-bit I2S -> remove DC -> shift >> 14 -> int16 WAV.
    // Trong khi ghi, neu frame van co RMS on dinh thi reset bo dem im lang.
    for (int i = 0; i < sampleCount && samplesRecorded < maxSamplesAllowed; i++)
    {
      int32_t raw = samples32[i] - dc;

      int32_t s14 = raw >> 14;
      if (s14 > 32767)
        s14 = 32767;
      if (s14 < -32768)
        s14 = -32768;

      int a14 = abs((int)s14);
      if (a14 > recPeak14)
        recPeak14 = a14;
      if (a14 > framePeak14)
        framePeak14 = a14;

      int16_t s16 = (int16_t)s14;
      audioFile.write((uint8_t *)&s16, sizeof(s16));

      totalDataBytes += sizeof(s16);
      samplesRecorded++;
    }

    // Reset bộ đếm im lặng CHỈ theo peak14, cùng logic với trigger.
    if (recPeak14 >= MIC_TRIGGER_PEAK14_LEVEL || framePeak14 >= MIC_TRIGGER_PEAK14_LEVEL)
    {
      lastRecordLoudMs = millis();
    }

    if (millis() - lastRecLogMs >= REC_LOG_INTERVAL_MS)
    {
      Serial.printf("[MIC] REC LOG peak14=%d threshold=%d silence_ms=%lu samples=%lu\n",
                    recPeak14, MIC_TRIGGER_PEAK14_LEVEL,
                    (unsigned long)(millis() - lastRecordLoudMs),
                    (unsigned long)samplesRecorded);
      recPeak14 = 0;
      lastRecLogMs = millis();
    }
  }

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
  if (!micReady || !sdReady || recordingInProgress || !micArmed || peerSyncQueuedByRequest)
    return;

  // Chi khoa mic khi dang thuc su sync/request sync.
  // Khong dung syncPending de khoa vinh vien, vi neu peer offline 3 lan
  // thi file .bin van duoc giu tren SD va mic phai duoc mo lai de ghi tiep.
  if (syncInProgress || peerSyncRequestInProgress)
    return;

  if (millis() - lastMicTriggerMs < MIC_COOLDOWN_MS)
    return;

  static int voiceFrames = 0;
  static unsigned long lastLogMs = 0;

  MicVoiceStats st = readMicVoiceStats();

  if (millis() - lastLogMs >= MIC_LOG_INTERVAL_MS)
  {
    Serial.printf("[MIC] peak14=%d threshold=%d voice=%s frames=%d/%d\n",
                  st.peak14, MIC_TRIGGER_PEAK14_LEVEL,
                  st.voiceLike ? "yes" : "no",
                  voiceFrames, MIC_VOICE_FRAMES_TO_TRIGGER);
    lastLogMs = millis();
  }

  if (st.voiceLike)
    voiceFrames++;
  else
    voiceFrames = 0;

  if (voiceFrames >= MIC_VOICE_FRAMES_TO_TRIGGER)
  {
    String wavPath = genMicFilename();
    String encPath = encryptedPathFromWavPath(wavPath);
    lastMicWavFile = encPath;
    lastMicTriggerMs = millis();
    micArmed = false;
    voiceFrames = 0;

    Serial.printf("[FLOW] Peak14 trigger peak14=%d threshold=%d\n",
                  st.peak14, MIC_TRIGGER_PEAK14_LEVEL);

    bool ok = recordTriggeredWavToSD(wavPath.c_str(), AUTO_RECORD_MS);

    if (ok)
    {
      Serial.println("[FLOW] Record done -> AES-GCM encrypt");
      bool encOk = aesGcmEncryptFile(wavPath, encPath, true);
      if (encOk)
      {
        Serial.println("[FLOW] Encrypt done -> priority peer sync");
        markLocalFileChanged("mic encrypted record " + encPath);
      }
      else
      {
        Serial.println("[FLOW] Encrypt failed -> skip sync");
      }
    }
    else
    {
      Serial.println("[FLOW] Record failed -> skip sync");
    }
  }
}

long extractFileSizeFromListJson(const String &listJson, const String &base)
{
  String name1 = "rec/" + base;
  String name2 = base;

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

    if (n == name1 || n == name2)
    {
      int si = listJson.indexOf("\"size\":", ne);
      if (si < 0)
        return -1;

      si += 7;
      while (si < (int)listJson.length() && listJson[si] == ' ')
        si++;

      String numStr = "";
      while (si < (int)listJson.length() && isDigit(listJson[si]))
      {
        numStr += listJson[si];
        si++;
      }

      if (numStr.length() == 0)
        return -1;

      return numStr.toInt();
    }

    pos = ne + 1;
  }

  return -1;
}

bool verifyPeerHasLocalFile(const String &localPath)
{
  if (!sdReady || localPath.length() == 0)
    return false;

  cleanupZeroByteRecFiles();

  String cleanPath = normalizeRecPath(localPath);
  if (!isRecEncPath(cleanPath))
  {
    Serial.printf("[PrioritySync] Ignore non-BIN local file in single verify: %s\n", cleanPath.c_str());
    return true;
  }

  size_t localSize = safeFileSize(cleanPath);
  if (!isValidEncSize(localSize))
  {
    Serial.printf("[PrioritySync] Delete invalid local BIN before single verify: %s size=%lu\n",
                  cleanPath.c_str(), (unsigned long)localSize);
    removeIfExists(cleanPath);
    return true;
  }

  String base = cleanPath;
  int slash = base.lastIndexOf('/');
  if (slash >= 0)
    base = base.substring(slash + 1);
  if (base.length() == 0)
    return false;

  String displayRec = "rec/" + base;

  String listJson = httpGetFromPeer("/file/list", 5000);
  if (listJson.length() == 0)
  {
    Serial.println("[PrioritySync] Verify: peer /file/list empty");
    return false;
  }

  long remoteSize = extractFileSizeFromListJson(listJson, base);
  bool hasName = (remoteSize >= 0);
  bool sizeOk = (isValidEncSize(localSize) && remoteSize == (long)localSize);

  Serial.printf("[PrioritySync] Verify peer has BIN '%s': name=%s size=%s localSize=%lu remoteSize=%ld\n",
                displayRec.c_str(),
                hasName ? "yes" : "no",
                sizeOk ? "yes" : "no",
                (unsigned long)localSize,
                remoteSize);

  return hasName && sizeOk;
}

bool verifyPeerHasAllLocalRecFiles()
{
  if (!sdReady)
    return false;

  cleanupZeroByteRecFiles();

  String listJson = httpGetFromPeer("/file/list", 5000);
  if (listJson.length() == 0 || listJson.indexOf("\"name\"") < 0)
  {
    Serial.println("[PrioritySync] Verify all BIN: peer /file/list empty");
    return false;
  }

  if (!SD.exists("/rec"))
  {
    Serial.println("[PrioritySync] Verify all BIN: local /rec missing");
    return true;
  }

  File root = SD.open("/rec");
  if (!root || !root.isDirectory())
  {
    Serial.println("[PrioritySync] Verify all BIN: local /rec is not a directory");
    if (root)
      root.close();
    return true;
  }

  int totalEnc = 0;
  int okCount = 0;
  int skipped = 0;

  File f = root.openNextFile();
  while (f)
  {
    if (!f.isDirectory())
    {
      String fullPath = normalizeRecPath(String(f.name()));
      size_t localSize = f.size();

      if (!isRecEncPath(fullPath))
      {
        skipped++;
        f.close();
        f = root.openNextFile();
        continue;
      }

      if (!isValidEncSize(localSize))
      {
        f.close();
        Serial.printf("[PrioritySync] Delete invalid BIN before verify-all: %s size=%lu\n",
                      fullPath.c_str(), (unsigned long)localSize);
        removeIfExists(fullPath);
        f = root.openNextFile();
        continue;
      }

      String base = fullPath;
      int slash = base.lastIndexOf('/');
      if (slash >= 0)
        base = base.substring(slash + 1);

      long remoteSize = extractFileSizeFromListJson(listJson, base);
      bool ok = (remoteSize >= 0 && remoteSize == (long)localSize);

      Serial.printf("[PrioritySync] Verify all BIN peer '%s': local=%lu remote=%ld -> %s\n",
                    base.c_str(),
                    (unsigned long)localSize,
                    remoteSize,
                    ok ? "OK" : "MISS/SIZE_DIFF");

      totalEnc++;
      if (ok)
        okCount++;
      else
      {
        f.close();
        root.close();
        return false;
      }
    }

    f.close();
    f = root.openNextFile();
    server.handleClient();
    delay(1);
  }

  root.close();

  Serial.printf("[PrioritySync] Verify all BIN peer done: %d/%d OK, skipped=%d\n", okCount, totalEnc, skipped);
  return totalEnc == 0 || okCount == totalEnc;
}

bool verifyBidirectionalRecBinSync()
{
  if (!sdReady)
    return false;

  cleanupZeroByteRecFiles();

  String peerList = httpGetFromPeer("/file/list", 5000);
  if (peerList.length() == 0 || peerList.indexOf("\"name\"") < 0)
  {
    Serial.println("[PrioritySync] Verify 2-way: peer /file/list empty");
    return false;
  }

  struct LocalBinInfo
  {
    String base;
    size_t size;
  };

  std::vector<LocalBinInfo> localBins;
  if (SD.exists("/rec"))
  {
    File root = SD.open("/rec");
    if (root && root.isDirectory())
    {
      File f = root.openNextFile();
      while (f)
      {
        if (!f.isDirectory())
        {
          String fullPath = normalizeRecPath(String(f.name()));
          size_t localSize = f.size();
          if (isRecEncPath(fullPath) && isValidEncSize(localSize))
          {
            String base = fullPath;
            int slash = base.lastIndexOf('/');
            if (slash >= 0)
              base = base.substring(slash + 1);
            localBins.push_back({base, localSize});
          }
        }
        f.close();
        f = root.openNextFile();
        server.handleClient();
        delay(1);
      }
      root.close();
    }
    else if (root)
    {
      root.close();
    }
  }

  int okLocalOnPeer = 0;
  for (auto &lb : localBins)
  {
    long remoteSize = extractFileSizeFromListJson(peerList, lb.base);
    bool ok = (remoteSize >= 0 && remoteSize == (long)lb.size);
    if (!ok)
    {
      Serial.printf("[PrioritySync] Verify 2-way local->peer missing/diff, ask peer pull once: %s local=%lu remote=%ld\n",
                    lb.base.c_str(), (unsigned long)lb.size, remoteSize);

      bool asked = false;
      WiFiClient c;
      if (c.connect(PEER_IP, PEER_HTTP_PORT))
      {
        c.printf("POST /sync?bounce=0 HTTP/1.1\r\nHost: %s\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", PEER_IP);
        unsigned long t = millis();
        while ((c.connected() || c.available()) && (millis() - t) < 5000)
        {
          if (c.available())
            c.read();
          server.handleClient();
          delay(2);
        }
        c.stop();
        asked = true;
      }

      if (asked)
      {
        delay(500);
        String peerList2 = httpGetFromPeer("/file/list", 5000);
        long remoteSize2 = extractFileSizeFromListJson(peerList2, lb.base);
        bool fixed = (remoteSize2 >= 0 && remoteSize2 == (long)lb.size);
        if (fixed)
        {
          remoteSize = remoteSize2;
          ok = true;
        }
      }

      if (!ok)
      {
        Serial.printf("[PrioritySync] Verify 2-way FAIL local->peer: %s local=%lu remote=%ld\n",
                      lb.base.c_str(), (unsigned long)lb.size, remoteSize);
        return false;
      }
    }
    okLocalOnPeer++;
  }

  int peerChecked = 0;
  int pos = 0;
  while (true)
  {
    int ni = peerList.indexOf("\"name\":\"", pos);
    if (ni < 0)
      break;
    ni += 8;
    int ne = peerList.indexOf("\"", ni);
    if (ne < 0)
      break;
    String n = peerList.substring(ni, ne);
    pos = ne + 1;

    String norm = normalizeRecPath(n);
    if (!isRecEncPath(norm))
      continue;

    String base = norm;
    int slash = base.lastIndexOf('/');
    if (slash >= 0)
      base = base.substring(slash + 1);

    long peerSize = extractFileSizeFromListJson(peerList, base);
    if (peerSize <= (long)MIN_VALID_ENC_REC_SIZE)
      continue;

    String localPath = "/rec/" + base;
    size_t localSize = safeFileSize(localPath);
    bool ok = (isValidEncSize(localSize) && (long)localSize == peerSize);
    if (!ok)
    {
      Serial.printf("[PrioritySync] Verify 2-way peer->local missing/diff, try pull: %s local=%lu peer=%ld\n",
                    base.c_str(), (unsigned long)localSize, peerSize);
      bool pulled = httpDownloadFileFromPeer(base, nullptr);
      size_t afterSize = safeFileSize(localPath);
      bool fixed = (pulled && isValidEncSize(afterSize) && (long)afterSize == peerSize);
      if (!fixed)
      {
        Serial.printf("[PrioritySync] Verify 2-way FAIL peer->local: %s local=%lu peer=%ld\n",
                      base.c_str(), (unsigned long)afterSize, peerSize);
        return false;
      }
      localSize = afterSize;
    }
    peerChecked++;
  }

  Serial.printf("[PrioritySync] Verify 2-way OK: local_on_peer=%d peer_on_local=%d\n",
                okLocalOnPeer, peerChecked);
  return true;
}

// ── JSON WAV info ─────────────────────────────────────────────
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

// ── Uptime / count ────────────────────────────────────────────
String formatUptime(uint32_t ms)
{
  uint32_t s = ms / 1000;
  uint32_t m = s / 60;
  s %= 60;
  uint32_t h = m / 60;
  m %= 60;
  char b[32];
  snprintf(b, sizeof(b), "%02d:%02d:%02d", h, m, s);
  return String(b);
}

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

// ── AP restore ────────────────────────────────────────────────
void restoreMyAP()
{
  WiFi.mode(WIFI_AP_STA);
  WiFi.setSleep(false);

  IPAddress apIP;
  apIP.fromString(MY_AP_IP_STR);
  IPAddress gw = apIP;
  IPAddress sn(255, 255, 255, 0);

  WiFi.softAPConfig(apIP, gw, sn);
  WiFi.softAP(MY_AP_SSID, MY_AP_PASSWORD, MY_AP_CHANNEL, MY_AP_HIDDEN, MY_AP_MAX_CON);

  server.begin();
  audioServer.begin();
  uploadServer.begin();

  Serial.printf("[WiFi] %s AP restored max_clients=%d clients=%d\n", MY_AP_SSID,
                MY_AP_MAX_CON, WiFi.softAPgetStationNum());
}

// ── Debug WiFi scan trước khi connect peer ─────────────────────
void debugScanPeerNetworks(const char *targetSsid)
{
  Serial.println("[WiFi] Scan before peer connect...");
  int n = WiFi.scanNetworks(false, true);
  bool found = false;
  if (n <= 0)
  {
    Serial.println("[WiFi] Scan result: no network found");
  }
  else
  {
    for (int i = 0; i < n; i++)
    {
      String ssid = WiFi.SSID(i);
      Serial.printf("  SSID[%d]=%s RSSI=%d CH=%d\n",
                    i,
                    ssid.c_str(),
                    WiFi.RSSI(i),
                    WiFi.channel(i));
      if (ssid == String(targetSsid))
        found = true;
    }
  }
  WiFi.scanDelete();
  Serial.printf("[WiFi] Target '%s': %s\n", targetSsid, found ? "FOUND" : "NOT FOUND");
}

// ── HTTP GET text tu Phantom-2 ────────────────────────────────
String httpGetFromPeer(const char *path, int timeoutMs = 6000)
{
  WiFiClient c;
  if (!c.connect(PEER_IP, PEER_HTTP_PORT))
    return "";

  c.printf("GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n", path, PEER_IP);

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

// ── Lay size tu JSON list cua peer ────────────────────────────
int32_t getRemoteFileSize(const String &listJson, const String &fname)
{
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
      int si = listJson.indexOf("\"size\":", ne);
      if (si < 0)
        return -1;
      si += 7;
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

// ── Download 1 file tu Phantom-2 ──────────────────────────────
bool httpDownloadFileFromPeer(const String &filename, WiFiClient *persist)
{
  String path = "/file/download?name=" + filename;

  // Chỉ tải file .bin hợp lệ; .wav không phải dữ liệu sync chính thức.
  if (!isRecEncPath(filename))
  {
    Serial.printf("[Sync] Refuse download non-BIN file: %s\n", filename.c_str());
    return false;
  }

  WiFiClient _own;
  WiFiClient *c = persist ? persist : &_own;

  if (!persist)
  {
    if (!c->connect(PEER_IP, PEER_HTTP_PORT))
    {
      Serial.printf("[Sync] HTTP connect FAILED for '%s'\n", filename.c_str());
      return false;
    }
  }
  else if (!c->connected())
  {
    c->stop();
    if (!c->connect(PEER_IP, PEER_HTTP_PORT))
    {
      Serial.printf("[Sync] HTTP reconnect FAILED for '%s'\n", filename.c_str());
      return false;
    }
  }

  c->printf("GET %s HTTP/1.1\r\nHost: %s\r\nConnection: keep-alive\r\n\r\n",
            path.c_str(), PEER_IP);

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

  String sdPath = normalizeRecPath(filename);
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

  uint8_t chunk[1024];
  size_t rx = 0;
  t = millis();

  while (rx < (size_t)contentLength && (c->connected() || c->available()) && (millis() - t) < 45000)
  {
    size_t av = c->available();
    if (av > 0)
    {
      size_t want = min(av, min((size_t)1024, (size_t)(contentLength - rx)));
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
    Serial.printf("[Sync] '%s' verify FAIL -> delete\n", filename.c_str());
    return false;
  }

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

  Serial.printf("[Sync] '%s' %d bytes -> OK heap=%d\n",
                filename.c_str(), rx, ESP.getFreeHeap());
  return true;
}

// ── Check peer busy before sync ───────────────────────────────
// Phantom-2 se bao busy=true khi dang ghi am hoac dang sync.
bool isPeerBusy()
{
  String body = httpGetFromPeer("/busy", 3000);
  if (body.length() == 0)
  {
    // Firmware cu chua co /busy: fallback check /status recording=true
    body = httpGetFromPeer("/status", 3000);
  }

  if (body.indexOf("\"busy\":true") >= 0 ||
      body.indexOf("\"recording\":true") >= 0 ||
      body.indexOf("\"sync_in_progress\":true") >= 0)
  {
    Serial.println("[Sync] Peer is BUSY -> skip this sync");
    syncMsg = "skip: Phantom-2 busy";
    return true;
  }

  return false;
}

// ── Dong bo tat ca file tu peer ve node nay ─────────────
bool syncFromPeer()
{
  if (syncInProgress)
  {
    syncMsg = "busy: Phantom-1 already syncing";
    Serial.println("[Sync] Local busy -> skip");
    return false;
  }

  syncInProgress = true;
  Serial.println("\n[Sync] == Bat dau ket noi peer ==");
  syncMsg = "connecting peer";

  WiFi.mode(WIFI_AP_STA);
  WiFi.setSleep(false);
  WiFi.disconnect(false);
  delay(200);
  debugScanPeerNetworks(PEER_SSID);
  Serial.printf("[WiFi] Connecting to %s ...\n", PEER_SSID);
  WiFi.begin(PEER_SSID, PEER_PASSWORD);

  int retries = 0;
  while (WiFi.status() != WL_CONNECTED && retries < 30)
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
    Serial.println("\n[Sync] FAILED: peer not found");
    syncMsg = "failed: peer not found";
    WiFi.disconnect(false);
    restoreMyAP();
    syncInProgress = false;
    return false;
  }

  Serial.printf("\n[Sync] Connected peer. STA IP: %s\n",
                WiFi.localIP().toString().c_str());
  delay(100);

  // IMPORTANT FIX:
  // Do NOT call isPeerBusy() here. When Phantom-2 has just finished recording,
  // it may report busy because it is waiting for Phantom-1 to pull the new file.
  // If Phantom-1 skips here, Phantom-2 can falsely think sync is done.
  // The real protection is on Phantom-2's POST /sync handler: it rejects sync
  // while recordingInProgress or syncInProgress is true.

  String listJson = "";
  for (int attempt = 0; attempt < 3; attempt++)
  {
    listJson = httpGetFromPeer("/file/list", 4000);
    if (listJson.length() > 10 && listJson.indexOf("\"name\"") >= 0)
      break;

    Serial.printf("[Sync] /file/list empty attempt %d/3\n", attempt + 1);
    unsigned long tw = millis();
    while (millis() - tw < 300)
    {
      server.handleClient();
      delay(5);
    }
  }

  int peerCount = 0;
  {
    int p = 0;
    while (listJson.indexOf("\"name\":\"", p) >= 0)
    {
      int ni = listJson.indexOf("\"name\":\"", p) + 8;
      int ne = listJson.indexOf("\"", ni);
      if (ne < 0)
        break;
      peerCount++;
      p = ne + 1;
    }
  }

  Serial.printf("[Sync] Peer list: %d file(s)\n", peerCount);

  if (listJson.indexOf("\"count\":0") >= 0 || listJson.indexOf("\"files\":[]") >= 0 || peerCount == 0)
  {
    Serial.println("[Sync] peer has no file");
    WiFi.disconnect(false);
    restoreMyAP();
    syncMsg = "ok: peer empty";
    syncInProgress = false;
    return false;
  }

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

  WiFiClient keepAlive;
  if (!keepAlive.connect(PEER_IP, PEER_HTTP_PORT))
  {
    Serial.println("[Sync] Keep-alive connect FAILED - fallback per-file");
    keepAlive.stop();
  }

  bool useKeepAlive = keepAlive.connected();
  Serial.printf("[Sync] Keep-alive: %s\n", useKeepAlive ? "ON" : "OFF");

  for (auto &fname : remoteFiles)
  {
    String path = normalizeRecPath(fname);
    int32_t remoteSize = getRemoteFileSize(listJson, fname);

    String fnameLow = fname;
    fnameLow.toLowerCase();

    // Chỉ đồng bộ file ghi âm chính thức đã mã hóa. .wav là file tạm, không sync.
    if (!isRecEncPath(fname))
    {
      Serial.printf("[Sync] Skip non-BIN file from peer: %s\n", fname.c_str());
      skipped++;
      continue;
    }

    if (remoteSize <= MIN_VALID_ENC_REC_SIZE)
    {
      Serial.printf("[Sync] Skip remote invalid/missing BIN: %s size=%d\n", fname.c_str(), (int)remoteSize);
      skipped++;
      continue;
    }

    if (SD.exists(path))
    {
      File f = SD.open(path, "r");
      size_t localSize = f ? f.size() : 0;
      if (f)
        f.close();

      if (remoteSize > MIN_VALID_ENC_REC_SIZE && (int32_t)localSize == remoteSize)
      {
        Serial.printf("[Sync] Skip '%s' - already exists (%d bytes)\n",
                      fname.c_str(), (int)localSize);
        skipped++;
        continue;
      }

      Serial.printf("[Sync] Update '%s' local=%d remote=%d\n",
                    fname.c_str(), (int)localSize, (int)remoteSize);
      SD.remove(path);

      bool ok = httpDownloadFileFromPeer(fname, useKeepAlive ? &keepAlive : nullptr);
      if (ok)
      {
        downloaded++;
        updated++;
      }
    }
    else
    {
      Serial.printf("[Sync] Download new '%s' (%d bytes)\n", fname.c_str(), remoteSize);
      bool ok = httpDownloadFileFromPeer(fname, useKeepAlive ? &keepAlive : nullptr);
      if (ok)
        downloaded++;
    }
  }

  if (useKeepAlive)
    keepAlive.stop();

  if (downloaded > 0)
    blinkLED(5, 100);

  WiFi.disconnect(false);
  delay(100);
  restoreMyAP();

  Serial.printf("[Sync] Result: downloaded=%d updated=%d skipped=%d total=%d\n",
                downloaded, updated, skipped, (int)remoteFiles.size());

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

  syncMsg = "ok: synced " + String(downloaded) + "/" + String(remoteFiles.size()) + " files from peer";
  syncInProgress = false;
  return (downloaded > 0);
}

// ── HTTP Handlers ─────────────────────────────────────────────
void handleStatus()
{
  float _bv = readBatteryVoltage();
  int _bp = batteryPercent(_bv);
  int _br = readBatteryRaw();

  server.send(200, "application/json",
              String("{\"node\":") + String(NODE_ID) +
                  ",\"ap_ssid\":\"" + MY_AP_SSID + "\"" +
                  ",\"ap_ip\":\"" + MY_AP_IP_STR + "\"" +
                  ",\"ap_clients\":" + String(WiFi.softAPgetStationNum()) +
                  ",\"peer_ssid\":\"" + PEER_SSID + "\"" +
                  ",\"peer_ip\":\"" + PEER_IP + "\"" +
                  ",\"uptime\":\"" + formatUptime(millis()) + "\"" +
                  ",\"free_heap\":" + String(ESP.getFreeHeap()) +
                  ",\"sync_done\":" + (syncDone ? "true" : "false") +
                  ",\"sync_failed\":" + (syncFailed ? "true" : "false") +
                  ",\"sync_in_progress\":" + String(syncInProgress ? "true" : "false") +
                  ",\"busy\":" + String((recordingInProgress || syncInProgress || peerSyncRequestInProgress) ? "true" : "false") +
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

void handleFileUploadDone()
{
  if (httpUploadOk)
  {
    markLocalFileChanged("HTTP upload " + httpUploadFilename);

    String resp = "{\"status\":\"ok\"";
    resp += ",\"filename\":\"" + httpUploadFilename + "\"";
    resp += ",\"path\":\"" + httpUploadPath + "\"";
    resp += ",\"size\":" + String(httpUploadSize);
    resp += ",\"sd_saved\":true}";
    server.send(200, "application/json", resp);
  }
  else
  {
    String resp = "{\"status\":\"fail\"";
    resp += ",\"filename\":\"" + httpUploadFilename + "\"";
    resp += ",\"path\":\"" + httpUploadPath + "\"";
    resp += ",\"size\":" + String(httpUploadSize);
    resp += ",\"sd_saved\":false";
    resp += ",\"error\":\"" + httpUploadError + "\"}";
    server.send(500, "application/json", resp);
  }

  Serial.printf("[UploadHTTP] done file='%s' size=%d ok=%s err=%s\n",
                httpUploadFilename.c_str(),
                (int)httpUploadSize,
                httpUploadOk ? "true" : "false",
                httpUploadError.c_str());
}

void handleFileUploadStream()
{
  HTTPUpload &upload = server.upload();

  if (upload.status == UPLOAD_FILE_START)
  {
    httpUploadSize = 0;
    httpUploadOk = false;
    httpUploadError = "none";
    httpUploadFilename = "";
    httpUploadPath = "";

    if (!sdReady)
    {
      httpUploadError = "sd not ready";
      return;
    }

    String xFilename = server.header("X-Filename");
    xFilename.trim();

    String rawName = xFilename.length() > 0 ? xFilename : upload.filename;
    rawName.trim();

    String saveAs = sanitizeFilename(rawName);
    if (saveAs.length() == 0)
      saveAs = genAutoFilename();

    if (!SD.exists("/rec"))
    {
      if (!SD.mkdir("/rec"))
      {
        httpUploadError = "cannot create /rec";
        return;
      }
    }

    httpUploadFilename = "rec/" + saveAs;
    httpUploadPath = "/rec/" + saveAs;

    if (SD.exists(httpUploadPath))
      SD.remove(httpUploadPath);

    httpUploadFile = SD.open(httpUploadPath, "w");
    if (!httpUploadFile)
    {
      httpUploadError = "sd open failed";
      return;
    }

    Serial.printf("[UploadHTTP] start '%s' -> '%s'\n",
                  rawName.c_str(), httpUploadPath.c_str());
  }
  else if (upload.status == UPLOAD_FILE_WRITE)
  {
    if (!httpUploadFile)
    {
      if (httpUploadError == "none")
        httpUploadError = "file not open";
      return;
    }

    size_t wr = httpUploadFile.write(upload.buf, upload.currentSize);
    httpUploadSize += wr;

    if (wr != upload.currentSize)
    {
      httpUploadError = "sd write failed";
    }
  }
  else if (upload.status == UPLOAD_FILE_END)
  {
    if (httpUploadFile)
      httpUploadFile.close();

    if (httpUploadError != "none")
    {
      if (httpUploadPath.length() > 0)
        SD.remove(httpUploadPath);
      httpUploadOk = false;
      return;
    }

    if (httpUploadSize == 0)
    {
      httpUploadError = "empty upload";
      if (httpUploadPath.length() > 0)
        SD.remove(httpUploadPath);
      httpUploadOk = false;
      return;
    }

    if (httpUploadPath.length() == 0 || !SD.exists(httpUploadPath))
    {
      httpUploadError = "file not saved";
      httpUploadOk = false;
      return;
    }

    File chk = SD.open(httpUploadPath, "r");
    if (!chk)
    {
      httpUploadError = "verify open failed";
      httpUploadOk = false;
      return;
    }

    size_t realSize = chk.size();
    chk.close();

    if (realSize != httpUploadSize)
    {
      httpUploadError = "verify size mismatch";
      SD.remove(httpUploadPath);
      httpUploadOk = false;
      return;
    }

    httpUploadOk = true;
    blinkLED(5, 80);

    Serial.printf("[UploadHTTP] saved '%s' %d bytes\n",
                  httpUploadPath.c_str(), (int)httpUploadSize);
  }
  else if (upload.status == UPLOAD_FILE_ABORTED)
  {
    if (httpUploadFile)
      httpUploadFile.close();

    if (httpUploadPath.length() > 0)
      SD.remove(httpUploadPath);

    httpUploadError = "upload aborted";
    httpUploadOk = false;
    Serial.println("[UploadHTTP] aborted");
  }
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
              ok ? "{\"status\":\"ok\",\"message\":\"File SD da xoa\"}"
                 : "{\"status\":\"ok\",\"message\":\"Khong co file SD de xoa\"}");
}

void handleFileDelete()
{
  String name = server.arg("name");
  name.trim();

  if (name.length() == 0)
  {
    server.send(400, "application/json", "{\"error\":\"missing name\"}");
    return;
  }

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
  Serial.printf("[SD] Delete '%s' -> %s\n", path.c_str(), ok ? "OK" : "FAIL");
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

void handleSync()
{
  bool allowBackSync = (server.arg("bounce") != "0");
  if (syncInProgress)
  {
    server.send(423, "application/json",
                "{\"status\":\"busy\",\"message\":\"local node is already syncing\"}");
    return;
  }

  // Sync được ưu tiên hơn ghi âm.
  // Nếu đang ghi, yêu cầu dừng bản ghi hiện tại, finalize WAV -> encrypt .bin,
  // rồi queue sync chạy ngay trong loop sau khi recordingInProgress=false.
  if (recordingInProgress)
  {
    stopRecordingForSyncRequested = true;
    peerSyncQueuedByRequest = true;
    micArmed = false;
    syncMsg = "peer sync requested: stopping recording first";
    server.send(200, "application/json",
                "{\"status\":\"ok\",\"message\":\"local node stopping recording, sync queued\"}");
    Serial.println("[Sync] Peer requested sync -> stop recording then sync");
    return;
  }

  server.send(200, "application/json",
              "{\"status\":\"ok\",\"message\":\"peer sync starting\"}");
  syncDone = syncFromPeer();
  syncFailed = !syncDone;

  // Sau khi node nay pull xong file tu peer, goi nguoc 1 lan de peer pull lai.
  // Dung query bounce=0 de chong loop ping-pong vo han.
  if (SYNC_INITIATOR && syncDone && allowBackSync && !recordingInProgress && !syncInProgress)
  {
    Serial.println("[Sync] Back-sync request to peer (one-shot)");
    requestPeerPullSync(false);
  }
}

void handleBusy()
{
  // syncPending cũng được báo là busy để peer biết chưa được mở mic: còn file local cần kéo ngược lại.
  bool busy = recordingInProgress || syncInProgress || peerSyncRequestInProgress || peerSyncQueuedByRequest || syncPending;
  String reason = recordingInProgress ? "recording" : (syncInProgress ? "syncing" : (peerSyncRequestInProgress ? "peer_sync_request" : (peerSyncQueuedByRequest ? "peer_sync_queued" : (syncPending ? "sync_pending" : "idle"))));
  String j = "{\"node\":" + String(NODE_ID);
  j += ",\"busy\":" + String(busy ? "true" : "false");
  j += ",\"recording\":" + String(recordingInProgress ? "true" : "false");
  j += ",\"sync_in_progress\":" + String(syncInProgress ? "true" : "false");
  j += ",\"peer_sync_request_in_progress\":" + String(peerSyncRequestInProgress ? "true" : "false");
  j += ",\"peer_sync_queued\":" + String(peerSyncQueuedByRequest ? "true" : "false");
  j += ",\"sync_pending\":" + String(syncPending ? "true" : "false");
  j += ",\"reason\":\"" + reason + "\"";
  j += ",\"sync_msg\":\"" + syncMsg + "\"";
  j += ",\"last_mic_file\":\"" + lastMicWavFile + "\"}";
  server.send(busy ? 423 : 200, "application/json", j);
}

// ── Priority sync: khi SD co file local moi/cap nhat thi bao peer keo ve ──
void markLocalFileChanged(const String &reason)
{
  if (!sdReady)
    return;

  if (!SYNC_INITIATOR)
  {
    syncPending = false;
    syncPendingReason = "none";
    prioritySyncFailCount = 0;
    syncDone = false;
    syncFailed = false;
    syncMsg = "local saved on Phantom-2; waiting Phantom-2 sync trigger";
    micArmed = true;
    lastMicTriggerMs = millis();
    Serial.println("[PrioritySync] Passive node: keep local file, wait for Phantom-2 trigger");
    return;
  }

  syncPending = true;
  syncPendingReason = reason;
  prioritySyncFailCount = 0;
  syncMsg = "pending peer sync: " + reason;

  // Uu tien dong bo: khoa trigger ghi am cho den khi dong bo xong
  micArmed = false;

  Serial.println("[PrioritySync] Pending -> " + reason);
}

bool requestPeerPullSync(bool requestBackSync)
{
  if (!sdReady || recordingInProgress || syncInProgress || peerSyncRequestInProgress)
    return false;

  // Pull-first strategy for initiator:
  // when Phantom-1 has new local data, first pull missing files from Phantom-2,
  // then ask Phantom-2 to pull back from Phantom-1.
  if (SYNC_INITIATOR && requestBackSync)
  {
    Serial.println("[PrioritySync] Pull-first: sync local from peer before trigger peer pull");
    bool prePullOk = syncFromPeer();
    Serial.printf("[PrioritySync] Pull-first result: %s\n", prePullOk ? "OK" : "SKIP/FAIL");
  }

  lastPriorityFailPeerNotFound = false;
  peerSyncRequestInProgress = true;
  syncMsg = "priority: ask peer to pull files";

  Serial.println("[PrioritySync] Connect peer and POST /sync");

  WiFi.mode(WIFI_AP_STA);
  WiFi.setSleep(false);
  WiFi.disconnect(false);
  delay(200);
  debugScanPeerNetworks(PEER_SSID);
  Serial.printf("[WiFi] Connecting to %s ...\n", PEER_SSID);
  WiFi.begin(PEER_SSID, PEER_PASSWORD);
  int retries = 0;
  while (WiFi.status() != WL_CONNECTED && retries < 30)
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
    Serial.println("\n[PrioritySync] peer not found");
    syncMsg = "pending: peer not found";
    lastPriorityFailPeerNotFound = true;
    WiFi.disconnect(false);
    restoreMyAP();
    peerSyncRequestInProgress = false;
    return false;
  }

  // Neu peer dang sync, cho doi peer roi gui /sync ngay thay vi bo qua ngay lap tuc.
  unsigned long waitBusyStart = millis();
  while (true)
  {
    String busyBody = httpGetFromPeer("/busy", 3000);
    bool peerBusySync = (busyBody.indexOf("\"sync_in_progress\":true") >= 0 ||
                         busyBody.indexOf("\"peer_sync_request_in_progress\":true") >= 0);

    // Sync co quyen uu tien hon ghi am cua peer, nen "recording":true khong chan.
    if (!peerBusySync)
      break;

    if (millis() - waitBusyStart >= PRIORITY_SYNC_WAIT_PEER_IDLE_MS)
    {
      Serial.println("[PrioritySync] peer syncing timeout -> retry later");
      syncMsg = "pending: peer syncing timeout";
      WiFi.disconnect(false);
      restoreMyAP();
      peerSyncRequestInProgress = false;
      return false;
    }

    syncMsg = "pending: waiting peer sync to finish";
    server.handleClient();
    delay(300);
  }

  WiFiClient c;
  bool ok = false;
  if (c.connect(PEER_IP, PEER_HTTP_PORT))
  {
    const char *syncPath = requestBackSync ? "/sync?bounce=1" : "/sync?bounce=0";
    c.printf("POST %s HTTP/1.1\r\nHost: %s\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", syncPath, PEER_IP);

    unsigned long t = millis();
    String resp = "";
    while ((c.connected() || c.available()) && (millis() - t) < 15000)
    {
      server.handleClient(); // cho peer co the keo /file/list va /file/download tu node nay
      if (c.available())
      {
        char ch = (char)c.read();
        resp += ch;
        t = millis();
        if (resp.indexOf("\r\n\r\n") >= 0 && resp.indexOf("\"status\":\"ok\"") >= 0)
        {
          ok = true;
          break;
        }
      }
      else
      {
        delay(5);
      }
    }
    c.stop();
  }

  if (!ok)
  {
    Serial.println("[PrioritySync] POST /sync failed or timeout");
    syncMsg = "pending: trigger peer sync failed";
    WiFi.disconnect(false);
    restoreMyAP();
    peerSyncRequestInProgress = false;
    return false;
  }

  // Doi peer kéo xong file của mình, sau đó nếu peer cũng có file dở
  // thì cho peer gọi ngược /sync để node nay kéo file đó về.
  // Chỉ mở mic khi: toàn bộ file /rec của node nay đã nằm trên peer VÀ peer hết sync_pending.
  ok = false;
  bool verified = false;
  unsigned long waitStart = millis();
  while (millis() - waitStart < 90000UL)
  {
    server.handleClient(); // cho peer kéo file và/hoặc gọi ngược POST /sync

    String b = httpGetFromPeer("/busy", 2500);
    bool peerStable = (b.length() > 0 &&
                       b.indexOf("\"sync_in_progress\":true") < 0 &&
                       b.indexOf("\"peer_sync_request_in_progress\":true") < 0 &&
                       b.indexOf("\"peer_sync_queued\":true") < 0 &&
                       b.indexOf("\"sync_pending\":true") < 0);

    if (!verified && b.length() > 0 && b.indexOf("\"sync_in_progress\":true") < 0)
      verified = verifyPeerHasAllLocalRecFiles();

    if (verified && peerStable)
    {
      ok = true;
      break;
    }

    delay(500);
  }

  WiFi.disconnect(false);
  restoreMyAP();

  peerSyncRequestInProgress = false;

  if (ok && verified)
  {
    syncPending = false;
    syncDone = true;
    syncFailed = false;
    syncMsg = "ok: peer pulled all local /rec BIN files";
    syncPendingReason = "none";
    prioritySyncFailCount = 0;
    micArmed = true;
    lastMicTriggerMs = millis();
    Serial.println("[PrioritySync] Done (one-way) -> recording can run again");
    return true;
  }

  syncMsg = "pending: wait/verify peer pull timeout";
  syncFailed = true;
  micArmed = false; // tiep tuc khoa mic, handlePrioritySync se retry
  Serial.println("[PrioritySync] Verify failed -> keep mic locked and retry");
  return false;
}

void handlePrioritySync()
{
  if (!SYNC_INITIATOR)
    return;

  if (!syncPending)
    return;

  cleanupZeroByteRecFiles();

  // Uu tien dong bo hon ghi am trong luc dang thu 1 lan.
  // Sau khi fail 1 lan thi se BO pending va MO mic de ghi tiep,
  // file .bin van nam tren SD card de tai/xu ly sau.
  micArmed = false;

  if (recordingInProgress || syncInProgress || peerSyncRequestInProgress)
    return;

  // Chong retry qua day neu peer khong tim thay/ban
  if (millis() - lastPrioritySyncAttemptMs < 3000UL)
    return;
  lastPrioritySyncAttemptMs = millis();

  bool ok = requestPeerPullSync();
  if (ok)
    return;

  prioritySyncFailCount++;
  Serial.printf("[PrioritySync] Retry failed %u/1\n", prioritySyncFailCount);

  if (prioritySyncFailCount >= 1)
  {
    if (lastPriorityFailPeerNotFound)
    {
      Serial.println("[PrioritySync] Failed 1/1 (peer not found) -> unlock mic and continue recording");
      syncPending = false;
      syncPendingReason = "none";
      syncDone = false;
      syncFailed = true;
      syncMsg = "offline: peer not found, continue local recording";
      micArmed = true;
      lastMicTriggerMs = millis();
    }
    else
    {
      // Keep waiting state: do not unlock mic until 2-way sync verification succeeds.
      Serial.println("[PrioritySync] Failed 1/1 -> keep waiting, mic stays locked, retry next cycle");
      syncDone = false;
      syncFailed = true;
      syncMsg = "pending: retry sync next cycle (mic locked until 2-way sync done)";
      micArmed = false;
    }
    prioritySyncFailCount = 0;
    lastPriorityFailPeerNotFound = false;

    peerSyncRequestInProgress = false;
    WiFi.disconnect(false);
    restoreMyAP();
  }
}

void handleAutoSync()
{
  if (millis() - lastAutoSyncMs < AUTO_SYNC_INTERVAL_MS)
    return;

  lastAutoSyncMs = millis();

  if (!sdReady || recordingInProgress || syncInProgress)
  {
    if (recordingInProgress)
      syncMsg = "skip: recording in progress";
    return;
  }

  Serial.println("[AutoSync] 60s tick -> try sync from peer");
  syncDone = syncFromPeer();
  syncFailed = !syncDone;
}

void handleQueuedPeerSyncRequest()
{
  if (!peerSyncQueuedByRequest)
    return;

  if (recordingInProgress || syncInProgress || peerSyncRequestInProgress)
    return;

  // Chạy trước priority sync local để đảm bảo yêu cầu /sync từ peer được xử lý ngay.
  peerSyncQueuedByRequest = false;
  micArmed = false;
  syncMsg = "peer requested sync: running now";
  Serial.println("[Sync] Queued peer sync -> running now");
  syncDone = syncFromPeer();
  syncFailed = !syncDone;
  restoreMyAP();
  lastMicTriggerMs = millis();

  if (!syncPending && !syncInProgress && !peerSyncRequestInProgress)
    micArmed = true;
}

void handleSyncStatus()
{
  int count = countSdFiles();

  String j = "{\"node\":" + String(NODE_ID);
  j += ",\"peer\":\"" + String(PEER_SSID) + "\"";
  j += ",\"file_count\":" + String(count);
  j += ",\"sd_used\":" + String(sdReady ? (uint32_t)(SD.usedBytes() / 1024) : 0);
  j += ",\"sd_free\":" + String(sdReady ? (uint32_t)((SD.totalBytes() - SD.usedBytes()) / 1024) : 0);
  j += ",\"uptime\":\"" + formatUptime(millis()) + "\"";
  j += ",\"free_heap\":" + String(ESP.getFreeHeap());
  j += ",\"sync_done\":" + String(syncDone ? "true" : "false");
  j += ",\"sync_failed\":" + String(syncFailed ? "true" : "false");
  j += ",\"sync_in_progress\":" + String(syncInProgress ? "true" : "false");
  j += ",\"busy\":" + String(syncInProgress ? "true" : "false");
  j += ",\"sync_msg\":\"" + syncMsg + "\"}";
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

void handleNotFound()
{
  server.send(404, "application/json", "{\"error\":\"not found\"}");
}

// ── Raw TCP Upload Server port 8081 ───────────────────────────
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

  if (!SD.exists("/rec"))
    SD.mkdir("/rec");

  String path = "/rec/" + saveAs;
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

  if (saved)
    markLocalFileChanged("TCP8081 upload " + saveAs);

  blinkLED(saved ? 5 : 2, 80);
  Serial.printf("[Upload8081] '%s' %d bytes -> %s\n", saveAs.c_str(), rx, saved ? "OK" : "FAIL");

  String resp = "{\"status\":\"" + String(saved ? "ok" : "fail") + "\""
                                                                   ",\"filename\":\"" +
                saveAs + "\""
                         ",\"size\":" +
                String(rx) +
                ",\"sd_saved\":" + String(saved ? "true" : "false") + "}";

  cli.printf("HTTP/1.0 %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s",
             saved ? "200 OK" : "500 Internal Server Error",
             (int)resp.length(), resp.c_str());
  cli.flush();
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

      String targetPath = normalizeRecPath(saveAs);
      bool sv = sdSaveAs(buf, rx, targetPath);

      if (sv)
        markLocalFileChanged("TCP8080 upload " + saveAs);

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

// ── Setup ─────────────────────────────────────────────────────
void setup()
{
  Serial.begin(115200);
  delay(500);

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  analogReadResolution(12);
  analogSetPinAttenuation(BATTERY_ADC_PIN, ADC_11db);

  Serial.println("\n==============================");
  Serial.println(" ESP32 PHANTOM-1 (APSTA + SD)");
  Serial.println("==============================");

  sdReady = setupSDCard();

  if (sdReady)
  {
    // Xóa mọi file 0KB còn sót trên toàn bộ SD ngay khi khởi động.
    // Sau đó tiếp tục cleanup riêng cho file ghi âm .wav/.bin lỗi.
    cleanupAllZeroByteFilesOnBoot();
    cleanupZeroByteRecFiles();
    syncMicCounterFromSd();
  }

  WiFi.mode(WIFI_AP_STA);
  WiFi.setSleep(false);

  IPAddress apIP;
  apIP.fromString(MY_AP_IP_STR);
  IPAddress gw = apIP;
  IPAddress sn(255, 255, 255, 0);

  WiFi.softAPConfig(apIP, gw, sn);
  WiFi.softAP(MY_AP_SSID, MY_AP_PASSWORD, MY_AP_CHANNEL, MY_AP_HIDDEN, MY_AP_MAX_CON);

  delay(200);
  Serial.printf("[AP] SSID: %s  IP: %s  max_clients=%d\n", MY_AP_SSID, WiFi.softAPIP().toString().c_str(), MY_AP_MAX_CON);
  digitalWrite(LED_PIN, HIGH);

  const char *collectHeaders[] = {"X-Filename", "Content-Length", "Content-Type"};
  server.collectHeaders(collectHeaders, 3);

  server.on("/status", HTTP_GET, handleStatus);
  server.on("/file/info", HTTP_GET, handleFileInfo);
  server.on("/file/list", HTTP_GET, handleFileList);
  server.on("/file/download", HTTP_GET, handleFileDownload);
  server.on("/file/upload", HTTP_POST, handleFileUploadDone, handleFileUploadStream);
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

  // Gio Viet Nam. Neu khong co internet thi khong anh huong dong bo file.
  configTime(7 * 3600, 0, "pool.ntp.org", "time.nist.gov");

  int fc = countSdFiles();
  Serial.printf("[SD] Current root files: %d. No startup sync.\n", fc);

  syncDone = false;
  syncFailed = false;
  syncMsg = "ready: priority sync after any local file write";

  Serial.printf("\n[Ready] %s endpoints:\n", MY_AP_SSID);
  Serial.printf("  WiFi: %s / %s\n", MY_AP_SSID, MY_AP_PASSWORD);
  Serial.printf("  GET  http://%s/status\n", MY_AP_IP_STR);
  Serial.printf("  GET  http://%s/file/list\n", MY_AP_IP_STR);
  Serial.printf("  GET  http://%s/file/download?name=photo.png\n", MY_AP_IP_STR);
  Serial.printf("  POST http://%s/file/upload  (X-Filename: myfile.txt)\n", MY_AP_IP_STR);
  Serial.printf("  POST http://%s/sync  -> sync from peer\n", MY_AP_IP_STR);
  Serial.printf("  GET  http://%s:8080/  (TCP WAV)\n", MY_AP_IP_STR);
  Serial.println("  Priority sync: any local file write -> ask Phantom-2 to pull; recording waits until sync done");
}

// ── Loop ──────────────────────────────────────────────────────
void loop()
{
  server.handleClient();

  // Peer sync request co uu tien cao nhat: neu peer yeu cau sync trong luc dang ghi,
  // thiet bi se dung ghi -> ma hoa -> chay sync truoc khi nghe mic tiep.
  handleQueuedPeerSyncRequest();

  // Priority sync chay truoc mic va auto-sync dinh ky
  handlePrioritySync();
  handleAutoMicRecord();
  // AutoSync disabled: only sync when upload or recording marks syncPending.
  // 60s auto-sync disabled by request; sync only runs after local file changes or POST /sync.
  // handleAutoSync();

  if (!micArmed && !recordingInProgress && !syncPending && !syncInProgress && !peerSyncRequestInProgress)
  {
    MicVoiceStats st = readMicVoiceStats();
    // Rearm theo peak14 thấp hơn nửa ngưỡng, không dùng rms15 nữa.
    if (st.peak14 < (MIC_TRIGGER_PEAK14_LEVEL / 2))
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

  // Auto-sync moi 60 giay. Neu peer busy thi bo qua lan do.
}
