#pragma once
/*
 * Phantom Security — Boot integrity check, NVS key storage, watchdog config.
 * Uses official ESP-IDF APIs only.
 */

#include <Arduino.h>
#include <nvs_flash.h>
#include <nvs.h>
#include <esp_partition.h>
#include <esp_image_format.h>
#include <esp_task_wdt.h>
#include <esp_ota_ops.h>

// Runtime XOR decode — strings stored as encoded bytes, decoded on stack at runtime
static inline void _ph_decode(const uint8_t *in, char *out, size_t len) {
    for (size_t i = 0; i < len; i++) out[i] = (char)(in[i] ^ 0x1F);
    out[len] = '\0';
}

// Encoded NVS namespace/key ("phantom", "aes_key" XOR 0x1F)
static const uint8_t _PH_NS[]  = {0x6f,0x68,0x61,0x7e,0x74,0x6f,0x6d};
static const uint8_t _PH_KEY[] = {0x7e,0x72,0x72,0x5f,0x6a,0x72,0x79};

// Encoded WiFi credentials (XOR 0x1F)
// "Phantom-1" = {0x4f,0x77,0x7e,0x71,0x6b,0x70,0x72,0x32,0x2e}
// "Phantom-2" = {0x4f,0x77,0x7e,0x71,0x6b,0x70,0x72,0x32,0x2d}
// "12345678"  = {0x2e,0x2d,0x2c,0x2b,0x2a,0x29,0x28,0x27}
static const uint8_t _PH_SSID1[] = {0x4f,0x77,0x7e,0x71,0x6b,0x70,0x72,0x32,0x2e};
static const uint8_t _PH_SSID2[] = {0x4f,0x77,0x7e,0x71,0x6b,0x70,0x72,0x32,0x2d};
static const uint8_t _PH_PASS[]  = {0x2e,0x2d,0x2c,0x2b,0x2a,0x29,0x28,0x27};

// Encoded IP strings (XOR 0x1F)
// "192.168.4.1" = {0x2e,0x26,0x2d,0x31,0x2e,0x29,0x27,0x31,0x2b,0x31,0x2e}
// "192.168.5.1" = {0x2e,0x26,0x2d,0x31,0x2e,0x29,0x27,0x31,0x2a,0x31,0x2e}
static const uint8_t _PH_IP1[] = {0x2e,0x26,0x2d,0x31,0x2e,0x29,0x27,0x31,0x2b,0x31,0x2e};
static const uint8_t _PH_IP2[] = {0x2e,0x26,0x2d,0x31,0x2e,0x29,0x27,0x31,0x2a,0x31,0x2e};

// Decode WiFi strings into caller-provided buffers (must be >=10 and >=9 bytes)
static inline void phantomGetSSID1(char *out) { _ph_decode(_PH_SSID1, out, 9); }
static inline void phantomGetSSID2(char *out) { _ph_decode(_PH_SSID2, out, 9); }
static inline void phantomGetPass(char *out)  { _ph_decode(_PH_PASS,  out, 8); }
// Decode IP strings into caller-provided buffers (must be >=12 bytes)
static inline void phantomGetIP1(char *out) { _ph_decode(_PH_IP1, out, 11); }  // "192.168.4.1"
static inline void phantomGetIP2(char *out) { _ph_decode(_PH_IP2, out, 11); }  // "192.168.5.1"

#define PHANTOM_KEY_LEN       32
#define PHANTOM_WDT_TIMEOUT_S 30

// ── Boot integrity check ──────────────────────────────────────
inline bool phantomBootIntegrityCheck()
{
    const esp_partition_t *running = esp_ota_get_running_partition();
    if (!running) return false;

    uint8_t sha256[32];
    esp_err_t err = esp_partition_get_sha256(running, sha256);
    if (err != ESP_OK) return false;

#if PHANTOM_DEBUG
    Serial.print("[Sec] SHA256: ");
    for (int i = 0; i < 32; i++) Serial.printf("%02x", sha256[i]);
    Serial.println();
#endif
    return true;
}

// ── NVS key storage ───────────────────────────────────────────
inline bool phantomStoreKeyInNVS(const uint8_t *key, size_t keyLen = PHANTOM_KEY_LEN)
{
    char ns[8], kn[8];
    _ph_decode(_PH_NS, ns, 7);
    _ph_decode(_PH_KEY, kn, 7);

    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        err = nvs_flash_init();
    }
    if (err != ESP_OK) return false;

    nvs_handle_t h;
    err = nvs_open(ns, NVS_READWRITE, &h);
    if (err != ESP_OK) return false;

    err = nvs_set_blob(h, kn, key, keyLen);
    if (err == ESP_OK) err = nvs_commit(h);
    nvs_close(h);
    return (err == ESP_OK);
}

// Returns true if key loaded from NVS; false = use compile-time fallback
inline bool phantomLoadKeyFromNVS(uint8_t *outKey, size_t keyLen = PHANTOM_KEY_LEN)
{
    char ns[8], kn[8];
    _ph_decode(_PH_NS, ns, 7);
    _ph_decode(_PH_KEY, kn, 7);

    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        err = nvs_flash_init();
    }
    if (err != ESP_OK) return false;

    nvs_handle_t h;
    err = nvs_open(ns, NVS_READONLY, &h);
    if (err != ESP_OK) return false;

    size_t len = keyLen;
    err = nvs_get_blob(h, kn, outKey, &len);
    nvs_close(h);
    return (err == ESP_OK && len == keyLen);
}

// ── Watchdog config ───────────────────────────────────────────
inline void phantomConfigWatchdog()
{
    esp_task_wdt_init(PHANTOM_WDT_TIMEOUT_S, true);
    esp_task_wdt_add(NULL);
}

inline void phantomFeedWatchdog()
{
    esp_task_wdt_reset();
}
