// Leonardo build of the node's GNSS path: UBX from the receiver on Serial1 -> epochs -> detection rules,
// with the result on the USB serial port. The boot self-test of the codec still runs first.
// Wiring (5 V board, 3.3 V receiver: level-shift Leonardo TX -> receiver RX): receiver TX -> pin 0 (RX1),
// receiver RX <- pin 1 (TX1). The receiver's default baud is 9600 (Matek M10Q-5883).
#include <Arduino.h>

#include "stz_codec.h"
#include "stz_detect.h"
#include "stz_gnss.h"
#include "stz_ubx.h"

static const char kMaster[] = "000102030405060708090a0b0c0d0e0f";
static const char kHex[] = "11000100afde173f05165b66b678f4cad06f8b5fc667c293ae03d96e";

static stz::ubx::Reader reader;
static stz::EpochBuilder builder;
static stz::Detector detector{stz::DetectConfig{}};
static uint32_t last_report_ms = 0;

static const char* codec_selftest() {
  uint8_t master[16], raw[stz::kMaxLen];
  size_t n = 0, len = 0;
  stz::from_hex(kMaster, strlen(kMaster), master, sizeof master, n);
  stz::from_hex(kHex, strlen(kHex), raw, sizeof raw, len);
  stz::Record r;
  return stz::reason(stz::decode(raw, len, 1, master, r));
}

static void configure_receiver() {
  uint8_t frame[96];
  size_t n = stz::ubx::build_boot_config(frame, sizeof frame);
  Serial1.write(frame, n);
}

void setup() {
  Serial.begin(115200);
  Serial1.begin(9600);
  configure_receiver();  // the module has no flash: this runs at every boot
}

void loop() {
  while (Serial1.available()) {
    stz::ubx::Kind k = reader.feed((uint8_t)Serial1.read());
    stz::Epoch e;
    if (k != stz::ubx::Kind::None && builder.add(k, reader, millis(), e)) {
      stz::Verdict v = detector.update(e);
      Serial.print(F("epoch sats="));
      Serial.print(e.sats_tracked);
      Serial.print(F(" cn0="));
      Serial.print(e.cn0_mean);
      Serial.print(F(" class="));
      Serial.println(v.event_class);
    }
  }
  // USB serial on the Leonardo opens after boot, so repeat the self-test instead of printing it once.
  if (millis() - last_report_ms > 5000) {
    last_report_ms = millis();
    Serial.print(F("STOZHER codec self-test: "));
    Serial.println(codec_selftest());
  }
}
