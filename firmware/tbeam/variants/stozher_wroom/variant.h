// STOZHER witness on an ESP32-WROOM-32 DevKit (38 pins) + RFM95W (SX1276) + u-blox GNSS, on a breadboard or perfboard.
// Adapted from Meshtastic's variants/esp32/diy/v1/variant.h (GPL-3.0). Differences, all for a bare DevKit:
// - GNSS on GPIO 16/17, not 12/15: GPIO 12 is a strapping pin, and a GNSS TX line holding it high at power-on makes the
//   ESP32 select 1.8 V flash and fail to boot.
// - SX1276 only (no SX126x or E22 TCXO) and no RF switch pins: the RFM95W switches internally.
// - Button on GPIO 0 (the DevKit's BOOT button); no battery divider or external-power pin, which float on a bare DevKit.

#define I2C_SDA 21 // optional OLED, or the GNSS module's compass
#define I2C_SCL 22

// GNSS: ESP32 RX <- GNSS TX on 16, ESP32 TX -> GNSS RX on 17
#undef GPS_RX_PIN
#undef GPS_TX_PIN
#define GPS_RX_PIN 16
#define GPS_TX_PIN 17
#define GPS_UBLOX

#define BUTTON_PIN 0 // BOOT button
#define LED_POWER 2  // the DevKit's LED

// RFM95W (SX1276)
#define USE_RF95
#define LORA_SCK 5
#define LORA_MISO 19
#define LORA_MOSI 27
#define LORA_CS 18
#define LORA_RESET 23
#define LORA_DIO0 26
#define LORA_DIO1 33
