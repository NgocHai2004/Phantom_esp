#include <Arduino.h>
#include <driver/i2s.h>
#include <SPIFFS.h>
#include <FS.h>

#define I2S_WS 25
#define I2S_SD 22
#define I2S_SCK 26

#define I2S_PORT I2S_NUM_0

#define SAMPLE_RATE 16000
#define SAMPLE_BITS 16
#define RECORD_TIME_SEC 5
#define WAV_FILE "/record.wav"
#define I2S_READ_LEN 1024

static const char b64_alphabet[] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

void base64EncodeAndPrint(Stream &out, const uint8_t *data, size_t len)
{
  char encoded[5];
  encoded[4] = '\0';

  for (size_t i = 0; i < len; i += 3)
  {
    uint32_t octet_a = i < len ? data[i] : 0;
    uint32_t octet_b = (i + 1) < len ? data[i + 1] : 0;
    uint32_t octet_c = (i + 2) < len ? data[i + 2] : 0;

    uint32_t triple = (octet_a << 16) | (octet_b << 8) | octet_c;

    encoded[0] = b64_alphabet[(triple >> 18) & 0x3F];
    encoded[1] = b64_alphabet[(triple >> 12) & 0x3F];
    encoded[2] = ((i + 1) < len) ? b64_alphabet[(triple >> 6) & 0x3F] : '=';
    encoded[3] = ((i + 2) < len) ? b64_alphabet[triple & 0x3F] : '=';

    out.print(encoded);
  }
}

void writeWavHeader(File file, uint32_t dataSize, uint32_t sampleRate, uint16_t bitsPerSample, uint16_t channels)
{
  uint32_t byteRate = sampleRate * channels * bitsPerSample / 8;
  uint16_t blockAlign = channels * bitsPerSample / 8;
  uint32_t chunkSize = 36 + dataSize;

  file.seek(0);

  file.write((const uint8_t *)"RIFF", 4);
  file.write((uint8_t *)&chunkSize, 4);
  file.write((const uint8_t *)"WAVE", 4);

  file.write((const uint8_t *)"fmt ", 4);
  uint32_t subchunk1Size = 16;
  uint16_t audioFormat = 1;
  file.write((uint8_t *)&subchunk1Size, 4);
  file.write((uint8_t *)&audioFormat, 2);
  file.write((uint8_t *)&channels, 2);
  file.write((uint8_t *)&sampleRate, 4);
  file.write((uint8_t *)&byteRate, 4);
  file.write((uint8_t *)&blockAlign, 2);
  file.write((uint8_t *)&bitsPerSample, 2);

  file.write((const uint8_t *)"data", 4);
  file.write((uint8_t *)&dataSize, 4);
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

  i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
  i2s_set_pin(I2S_PORT, &pin_config);
  i2s_zero_dma_buffer(I2S_PORT);
}

void listFiles()
{
  File root = SPIFFS.open("/");
  File file = root.openNextFile();

  Serial.println("Danh sach file:");
  while (file)
  {
    Serial.print(" - ");
    Serial.print(file.name());
    Serial.print(" (");
    Serial.print(file.size());
    Serial.println(" bytes)");
    file = root.openNextFile();
  }
}

void recordWav()
{
  if (SPIFFS.exists(WAV_FILE))
  {
    SPIFFS.remove(WAV_FILE);
  }

  File audioFile = SPIFFS.open(WAV_FILE, FILE_WRITE);
  if (!audioFile)
  {
    Serial.println("Khong mo duoc file de ghi");
    return;
  }

  uint8_t emptyHeader[44] = {0};
  audioFile.write(emptyHeader, 44);

  uint8_t i2sData[I2S_READ_LEN];
  size_t bytesRead = 0;
  uint32_t totalDataBytes = 0;
  uint32_t totalSamplesNeeded = SAMPLE_RATE * RECORD_TIME_SEC;
  uint32_t samplesRecorded = 0;

  Serial.println("REC_START");
  Serial.println("Noi vao mic trong 5 giay...");

  while (samplesRecorded < totalSamplesNeeded)
  {
    if (i2s_read(I2S_PORT, (void *)i2sData, I2S_READ_LEN, &bytesRead, portMAX_DELAY) != ESP_OK)
    {
      Serial.println("Loi i2s_read");
      break;
    }

    int32_t *samples32 = (int32_t *)i2sData;
    int sampleCount = bytesRead / 4;

    for (int i = 0; i < sampleCount && samplesRecorded < totalSamplesNeeded; i++)
    {
      int32_t s32 = samples32[i] >> 14; // tang gain vua phai
      if (s32 > 32767)
        s32 = 32767;
      if (s32 < -32768)
        s32 = -32768;
      int16_t s16 = (int16_t)s32;

      audioFile.write((uint8_t *)&s16, sizeof(s16));
      totalDataBytes += sizeof(s16);
      samplesRecorded++;
    }
  }

  writeWavHeader(audioFile, totalDataBytes, SAMPLE_RATE, SAMPLE_BITS, 1);
  audioFile.close();

  File f = SPIFFS.open(WAV_FILE, FILE_READ);
  if (f)
  {
    Serial.print("REC_DONE size=");
    Serial.println(f.size());
    f.close();
  }
  else
  {
    Serial.println("REC_DONE but cannot reopen file");
  }
}

void dumpWavBase64()
{
  File f = SPIFFS.open(WAV_FILE, FILE_READ);
  if (!f)
  {
    Serial.println("Khong tim thay /record.wav");
    return;
  }

  Serial.println("WAV_BASE64_BEGIN");

  const size_t bufSize = 384;
  uint8_t buf[bufSize];

  while (f.available())
  {
    size_t n = f.read(buf, bufSize);
    base64EncodeAndPrint(Serial, buf, n);
    Serial.println();
    delay(2);
  }

  Serial.println("WAV_BASE64_END");
  f.close();
}

void printHelp()
{
  Serial.println();
  Serial.println("Lenh:");
  Serial.println("  r : ghi 5 giay vao /record.wav");
  Serial.println("  d : dump /record.wav dang base64");
  Serial.println("  l : liet ke file");
  Serial.println("  h : help");
  Serial.println();
}

void setup()
{
  Serial.begin(115200);
  delay(1500);

  Serial.println();
  Serial.println("INMP441 WAV Recorder + Dump");

  if (!SPIFFS.begin(true))
  {
    Serial.println("Mount SPIFFS that bai");
    while (1)
      delay(1000);
  }

  setupI2S();
  printHelp();
  listFiles();
}

void loop()
{
  if (Serial.available())
  {
    char c = Serial.read();

    if (c == 'r' || c == 'R')
    {
      recordWav();
    }
    else if (c == 'd' || c == 'D')
    {
      dumpWavBase64();
    }
    else if (c == 'l' || c == 'L')
    {
      listFiles();
    }
    else if (c == 'h' || c == 'H')
    {
      printHelp();
    }
  }
}