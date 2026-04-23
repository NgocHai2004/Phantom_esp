#include <Arduino.h>
#include <driver/i2s.h>
#include <FS.h>
#include <SD.h>
#include <SPI.h>
#include "mbedtls/base64.h"

#define I2S_WS 25
#define I2S_SD 22
#define I2S_SCK 26
#define I2S_PORT I2S_NUM_0

#define SAMPLE_RATE 16000
#define SAMPLE_BITS 16
#define RECORD_TIME_SEC 5
#define I2S_READ_LEN 1024
#define LED_PIN 2

// SD card pins
#define SD_CS 5
#define SD_SCK 18
#define SD_MISO 19
#define SD_MOSI 23

String currentWavFile = "/record.wav";

void writeWavHeader(File file, uint32_t dataSize, uint32_t sampleRate, uint16_t bitsPerSample, uint16_t channels)
{
  uint32_t byteRate = sampleRate * channels * bitsPerSample / 8;
  uint16_t blockAlign = channels * bitsPerSample / 8;
  uint32_t chunkSize = 36 + dataSize;

  file.seek(0);

  file.write((const uint8_t *)"RIFF", 4);
  file.write((const uint8_t *)&chunkSize, 4);
  file.write((const uint8_t *)"WAVE", 4);

  file.write((const uint8_t *)"fmt ", 4);
  uint32_t subchunk1Size = 16;
  uint16_t audioFormat = 1;
  file.write((const uint8_t *)&subchunk1Size, 4);
  file.write((const uint8_t *)&audioFormat, 2);
  file.write((const uint8_t *)&channels, 2);
  file.write((const uint8_t *)&sampleRate, 4);
  file.write((const uint8_t *)&byteRate, 4);
  file.write((const uint8_t *)&blockAlign, 2);
  file.write((const uint8_t *)&bitsPerSample, 2);

  file.write((const uint8_t *)"data", 4);
  file.write((const uint8_t *)&dataSize, 4);
}

void setupI2S()
{
  i2s_config_t i2s_config = {
      .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
      .sample_rate = SAMPLE_RATE,
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

  esp_err_t e1 = i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
  esp_err_t e2 = i2s_set_pin(I2S_PORT, &pin_config);

  if (e1 != ESP_OK || e2 != ESP_OK)
  {
    Serial.printf("[ERR] I2S init fail install=%d setpin=%d\n", e1, e2);
    while (1)
      delay(1000);
  }

  i2s_zero_dma_buffer(I2S_PORT);
}

bool setupSD()
{
  SPI.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);

  if (!SD.begin(SD_CS, SPI))
  {
    Serial.println("[SD] Mount fail");
    return false;
  }

  uint8_t cardType = SD.cardType();
  if (cardType == CARD_NONE)
  {
    Serial.println("[SD] No card");
    return false;
  }

  uint64_t sizeMB = SD.cardSize() / (1024 * 1024);
  Serial.printf("[SD] OK, size=%llu MB\n", sizeMB);
  return true;
}

void printHelp()
{
  Serial.println("CMD: help | sdinfo | ls | rec | dump <file>");
}

void listFiles(fs::FS &fs, const char *dirname)
{
  File root = fs.open(dirname);
  if (!root || !root.isDirectory())
  {
    Serial.println("[LS] Open dir fail");
    return;
  }

  Serial.println("[LS_BEGIN]");
  File file = root.openNextFile();
  while (file)
  {
    if (!file.isDirectory())
    {
      Serial.printf("%s\t%u\n", file.name(), (unsigned)file.size());
    }
    file = root.openNextFile();
  }
  Serial.println("[LS_END]");
  Serial.flush();
}

void recordWavToSD(const char *path)
{
  if (SD.exists(path))
  {
    SD.remove(path);
  }

  File audioFile = SD.open(path, FILE_WRITE);
  if (!audioFile)
  {
    Serial.println("[REC] Cannot open file");
    return;
  }

  uint8_t emptyHeader[44] = {0};
  audioFile.write(emptyHeader, 44);

  uint8_t i2sData[I2S_READ_LEN];
  size_t bytesRead = 0;
  uint32_t totalDataBytes = 0;
  uint32_t totalSamplesNeeded = SAMPLE_RATE * RECORD_TIME_SEC;
  uint32_t samplesRecorded = 0;

  int32_t pcmMin = 32767;
  int32_t pcmMax = -32768;

  Serial.println("[REC_BEGIN]");
  Serial.flush();
  digitalWrite(LED_PIN, HIGH);

  while (samplesRecorded < totalSamplesNeeded)
  {
    if (i2s_read(I2S_PORT, (void *)i2sData, I2S_READ_LEN, &bytesRead, portMAX_DELAY) != ESP_OK)
    {
      Serial.println("[REC] i2s_read fail");
      break;
    }

    int32_t *samples32 = (int32_t *)i2sData;
    int sampleCount = bytesRead / 4;

    int64_t sum = 0;
    for (int i = 0; i < sampleCount; i++)
    {
      sum += samples32[i];
    }
    int32_t dc = sampleCount ? (int32_t)(sum / sampleCount) : 0;

    for (int i = 0; i < sampleCount && samplesRecorded < totalSamplesNeeded; i++)
    {
      int32_t s32 = (samples32[i] - dc) >> 13;
      if (s32 > 32767)
        s32 = 32767;
      if (s32 < -32768)
        s32 = -32768;

      int16_t s16 = (int16_t)s32;
      if (s16 < pcmMin)
        pcmMin = s16;
      if (s16 > pcmMax)
        pcmMax = s16;

      audioFile.write((uint8_t *)&s16, sizeof(s16));
      totalDataBytes += sizeof(s16);
      samplesRecorded++;
    }
  }

  writeWavHeader(audioFile, totalDataBytes, SAMPLE_RATE, SAMPLE_BITS, 1);
  audioFile.flush();
  audioFile.close();

  digitalWrite(LED_PIN, LOW);
  Serial.printf("[REC_DONE] %s size=%u min=%ld max=%ld\n",
                path, (unsigned)(totalDataBytes + 44), (long)pcmMin, (long)pcmMax);
  Serial.flush();
}

void dumpFileBase64(const char *path)
{
  File f = SD.open(path, FILE_READ);
  if (!f)
  {
    Serial.printf("[ERR] File not found: %s\n", path);
    Serial.flush();
    return;
  }

  uint32_t fileSize = (uint32_t)f.size();
  Serial.printf("[DUMP] %s size=%u\n", path, fileSize);
  Serial.printf("WAV_BEGIN %u\n", fileSize);
  Serial.flush();

  const size_t bufSize = 384;
  uint8_t buf[bufSize];
  unsigned char outB64[600];
  size_t outLen = 0;

  while (f.available())
  {
    size_t n = f.read(buf, bufSize);
    if (n == 0)
      break;

    int rc = mbedtls_base64_encode(outB64, sizeof(outB64), &outLen, buf, n);
    if (rc != 0)
    {
      Serial.printf("[ERR] base64 encode fail rc=%d\n", rc);
      break;
    }

    Serial.write(outB64, outLen);
    Serial.write('\n');
    Serial.flush();
    delay(2);
  }

  Serial.println("WAV_END");
  Serial.flush();
  f.close();
}

void processCommand(String cmd)
{
  cmd.trim();
  if (!cmd.length())
    return;

  if (cmd == "help")
  {
    printHelp();
    Serial.flush();
    return;
  }

  if (cmd == "sdinfo")
  {
    setupSD();
    Serial.flush();
    return;
  }

  if (cmd == "ls")
  {
    listFiles(SD, "/");
    return;
  }

  if (cmd == "rec")
  {
    currentWavFile = "/record_" + String((uint32_t)millis()) + ".wav";
    recordWavToSD(currentWavFile.c_str());
    return;
  }

  if (cmd.startsWith("dump "))
  {
    String path = cmd.substring(5);
    path.trim();
    if (!path.startsWith("/"))
      path = "/" + path;
    dumpFileBase64(path.c_str());
    return;
  }

  Serial.printf("[ERR] Unknown cmd: %s\n", cmd.c_str());
  Serial.flush();
}

void setup()
{
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  delay(1500);

  Serial.println("\nESP32 MIC + SD TEST");
  setupI2S();
  setupSD();
  printHelp();
  Serial.flush();
}

void loop()
{
  if (Serial.available())
  {
    String cmd = Serial.readStringUntil('\n');
    processCommand(cmd);
  }
}