// Leonardo size probe: the same boot self-test as main.cpp, on an 8-bit board with 2.5 KB of RAM.
#include <Arduino.h>

#include "stz_codec.h"

static const char kMaster[] = "000102030405060708090a0b0c0d0e0f";
static const char kHex[] = "11000100afde173f05165b66b678f4cad06f8b5fc667c293ae03d96e";

static void selftest() {
  uint8_t master[16], raw[stz::kMaxLen];
  size_t n = 0, len = 0;
  stz::from_hex(kMaster, strlen(kMaster), master, sizeof master, n);
  stz::from_hex(kHex, strlen(kHex), raw, sizeof raw, len);
  stz::Record r;
  stz::Status s = stz::decode(raw, len, 1, master, r);
  Serial.print(F("STOZHER codec self-test: "));
  Serial.println(stz::reason(s));
}

// USB serial on the Leonardo opens after boot, so repeat the result instead of printing it once.
void setup() { Serial.begin(115200); }

void loop() {
  selftest();
  delay(2000);
}
