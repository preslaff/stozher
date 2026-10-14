// Witness node firmware, step 1: the codec. Boot self-test against a golden vector; the node itself
// (GNSS, detection, KIM1, mesh) comes in later steps.
#include <Arduino.h>

#include "stz_codec.h"

// Test key and vector from tests/vectors/witness_records.json (core_event_jamming). Never a real key.
static const char kMaster[] = "000102030405060708090a0b0c0d0e0f";
static const char kHex[] = "11000100afde173f05165b66b678f4cad06f8b5fc667c293ae03d96e";

void setup() {
  Serial.begin(115200);
  delay(500);
  uint8_t master[16], raw[stz::kMaxLen];
  size_t n = 0, len = 0;
  stz::from_hex(kMaster, strlen(kMaster), master, sizeof master, n);
  stz::from_hex(kHex, strlen(kHex), raw, sizeof raw, len);
  stz::Record r;
  stz::Status s = stz::decode(raw, len, 1, master, r);
  Serial.printf("STOZHER codec self-test: %s (lat %ld lon %ld)\n", stz::reason(s), (long)r.lat_e7, (long)r.lon_e7);
}

void loop() { delay(1000); }
