// Leonardo build of the witness node: the same decision loop as the ESP32 (stz_node), with an 8-bit board's
// resources: GNSS on the hardware UART (Serial1), KIM1 on a software UART, sequence numbers in EEPROM.
//
// STATUS: compiles and fits; the codec self-test has run on a real Leonardo, the rest has NOT been run (no
// receiver and no KIM1 wired). Pins are placeholders. The Leonardo's I/O is 5 V, the receiver and the KIM1's
// serial lines are 3.3 V: level-shift Leonardo TX -> module RX on both. No signed-ack verification on this board
// (does not fit); records go out and are retransmitted by policy only. It uses the public test key.
#include <Arduino.h>
#include <EEPROM.h>
#include <SoftwareSerial.h>

#include "stz_codec.h"
#include "stz_node.h"
#include "stz_ubx.h"

#define KIM1_RX_PIN 8      // Leonardo pins 8-11 support software-serial receive
#define KIM1_TX_PIN 9
#define KIM1_STATUS_PIN 7  // KIM1 pin 11 (Tx_STATUS), 3.3 V output: fine into a 5 V input

namespace {

class SoftKim1 : public stz::kim1::Port {
 public:
  SoftKim1() : s_(KIM1_RX_PIN, KIM1_TX_PIN) {}
  void begin() {
    s_.begin(4800);
    pinMode(KIM1_STATUS_PIN, INPUT);
  }
  size_t write(const uint8_t* d, size_t n) override { return s_.write(d, n); }
  int read() override { return s_.available() ? s_.read() : -1; }
  bool tx_status() override { return digitalRead(KIM1_STATUS_PIN) == HIGH; }

 private:
  SoftwareSerial s_;
};

// EEPROM layout per key id: 0xA5 marker, then the next sequence number (little endian) and its complement.
class EepromSeqStore : public stz::SeqStore {
 public:
  Result load(uint8_t key_id, uint16_t& next) override {
    int a = base(key_id);
    if (EEPROM.read(a) != 0xA5) return Result::Missing;
    uint16_t v = (uint16_t)(EEPROM.read(a + 1) | EEPROM.read(a + 2) << 8);
    uint16_t c = (uint16_t)(EEPROM.read(a + 3) | EEPROM.read(a + 4) << 8);
    if ((uint16_t)~v != c) return Result::Error;  // torn write: do not guess
    next = v;
    return Result::Ok;
  }
  bool save(uint8_t key_id, uint16_t next) override {
    int a = base(key_id);
    EEPROM.update(a + 1, (uint8_t)next);
    EEPROM.update(a + 2, (uint8_t)(next >> 8));
    EEPROM.update(a + 3, (uint8_t)(~next));
    EEPROM.update(a + 4, (uint8_t)((uint16_t)~next >> 8));
    EEPROM.update(a, 0xA5);  // the marker last: a half-written record is never believed
    return true;
  }

 private:
  static int base(uint8_t key_id) { return (key_id & 15) * 5; }
};

SoftKim1 kim1_port;
EepromSeqStore seq_store;
stz::ubx::Reader reader;
stz::EpochBuilder builder;
stz::Tx* tx = nullptr;
stz::Node* node = nullptr;

}  // namespace

void setup() {
  Serial.begin(115200);
  Serial1.begin(9600);  // Matek M10Q default
  kim1_port.begin();
  stz::TxConfig tc;
  tc.node_id = 1;  // public test node, see the header
  uint8_t master[16];
  for (int i = 0; i < 16; i++) master[i] = (uint8_t)i;
  stz::Keys keys;
  stz::derive_keys(master, tc.node_id, tc.key_id, keys);
  tx = new stz::Tx(tc, keys, kim1_port, seq_store);
  node = new stz::Node(stz::DetectConfig{}, *tx);
  uint8_t frame[96];
  size_t n = stz::ubx::build_boot_config(frame, sizeof frame);
  Serial1.write(frame, n);
}

void loop() {
  uint32_t now = millis();
  while (Serial1.available()) {
    stz::ubx::Kind k = reader.feed((uint8_t)Serial1.read());
    stz::Epoch e;
    if (k == stz::ubx::Kind::None || !builder.add(k, reader, now, e)) continue;
    stz::Verdict v = node->on_epoch(e, now);
    if (v.changed && !v.blanked) {
      Serial.print(F("class "));
      Serial.println(v.event_class);
    }
  }
  node->tick(now);
}
