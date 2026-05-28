#include <stdio.h>
#include <string.h>

#include "esp_err.h"
#include "esp_log.h"
#include "esp_vfs_fat.h"
#include "driver/spi_common.h"
#include "driver/spi_master.h"
#include "sdmmc_cmd.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define SD_CS    3
#define SD_SCK   23
#define SD_MOSI  24
#define SD_MISO  25

static const char *TAG = "sd_test";

void app_main(void)
{
    vTaskDelay(pdMS_TO_TICKS(1500));
    ESP_LOGI(TAG, "=== ESP32-C5 microSD TEST ===");
    ESP_LOGI(TAG, "Khoi tao SPI...");

    spi_bus_config_t bus_cfg = {
        .mosi_io_num = SD_MOSI,
        .miso_io_num = SD_MISO,
        .sclk_io_num = SD_SCK,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = 4000,
    };

    esp_err_t ret = spi_bus_initialize(SPI2_HOST, &bus_cfg, SPI_DMA_CH_AUTO);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "FAIL: spi_bus_initialize loi: %s", esp_err_to_name(ret));
        return;
    }

    sdspi_device_config_t slot_config = SDSPI_DEVICE_CONFIG_DEFAULT();
    slot_config.gpio_cs = SD_CS;
    slot_config.host_id = SPI2_HOST;

    esp_vfs_fat_sdmmc_mount_config_t mount_config = {
        .format_if_mount_failed = false,
        .max_files = 5,
        .allocation_unit_size = 16 * 1024
    };

    sdmmc_card_t *card = NULL;
    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.slot = SPI2_HOST;
    host.max_freq_khz = 1000;

    ESP_LOGI(TAG, "Dang khoi tao the nho...");
    ret = esp_vfs_fat_sdspi_mount("/sdcard", &host, &slot_config, &mount_config, &card);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "FAIL: Khong nhan duoc the nho! (%s)", esp_err_to_name(ret));
        ESP_LOGI(TAG, "Kiem tra lai day:");
        ESP_LOGI(TAG, "VCC  -> 3V3");
        ESP_LOGI(TAG, "GND  -> GND");
        ESP_LOGI(TAG, "SCK  -> GPIO23");
        ESP_LOGI(TAG, "MOSI -> GPIO24");
        ESP_LOGI(TAG, "MISO -> GPIO25");
        ESP_LOGI(TAG, "CS   -> GPIO3");
        ESP_LOGI(TAG, "The nho nen format FAT32");
        spi_bus_free(SPI2_HOST);
        return;
    }

    ESP_LOGI(TAG, "OK: Da nhan the nho!");
    sdmmc_card_print_info(stdout, card);

    const char *path = "/sdcard/test.txt";
    FILE *f = fopen(path, "a");
    if (!f) {
        ESP_LOGE(TAG, "FAIL: Khong ghi duoc file %s", path);
        esp_vfs_fat_sdcard_unmount("/sdcard", card);
        spi_bus_free(SPI2_HOST);
        return;
    }
    fprintf(f, "ESP32-C5 microSD OK\n");
    fclose(f);
    ESP_LOGI(TAG, "OK: Da ghi file /test.txt");

    f = fopen(path, "r");
    if (!f) {
        ESP_LOGE(TAG, "FAIL: Khong doc duoc file %s", path);
        esp_vfs_fat_sdcard_unmount("/sdcard", card);
        spi_bus_free(SPI2_HOST);
        return;
    }

    ESP_LOGI(TAG, "Noi dung file:");
    char line[128];
    while (fgets(line, sizeof(line), f)) {
        printf("%s", line);
    }
    fclose(f);

    ESP_LOGI(TAG, "=== TEST HOAN TAT ===");
    esp_vfs_fat_sdcard_unmount("/sdcard", card);
    spi_bus_free(SPI2_HOST);
}
