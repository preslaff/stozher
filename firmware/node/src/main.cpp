// STOZHER witness node, XIAO ESP32-S3: GNSS (UBX) -> epochs -> detection -> signed record -> KIM1.
//
// STATUS: compiles; NOT run on hardware (no KIM1, no receiver wired yet). The pin numbers below are placeholders
// until the node is wired. Provisioning (node id, key) comes from NVS; without it the node falls back to the
// PUBLIC test key from tests/vectors and says so on the serial port, so test records can never be mistaken for
// a real node's.
#include <Arduino.h>
#include <Preferences.h>

#include "stz_detect.h"
#include "stz_gnss.h"
#include "stz_mesh.h"
#include "stz_node.h"
#include "stz_tx.h"
#include "stz_ubx.h"

// Placeholder pin plan (the XIAO has 11 usable GPIOs). UART0 (D6/D7) goes to the T-Beam's Serial module,
// D4/D5 stay free for the Grove I2C sensors, D2/D3 for the buzzer and the button.
#ifndef STZ_GNSS_RX
#define STZ_GNSS_RX D9
#define STZ_GNSS_TX D10
#endif
#ifndef STZ_KIM1_RX
#define STZ_KIM1_RX D8
#define STZ_KIM1_TX D0
#define STZ_KIM1_STATUS D1
#endif
#ifndef STZ_MESH_BAUD
#define STZ_MESH_BAUD 38400  // must match serial.baud on the T-Beam (check its Serial module settings)
#endif

namespace {

class UartKim1 : public stz::kim1::Port {
 public:
  UartKim1(HardwareSerial& s, int status_pin) : s_(s), pin_(status_pin) {}
  void begin() {
    s_.begin(4800, SERIAL_8N1, STZ_KIM1_RX, STZ_KIM1_TX);  // the KIM1's fixed UART settings
    pinMode(pin_, INPUT);
  }
  size_t write(const uint8_t* d, size_t n) override { return s_.write(d, n); }
  int read() override { return s_.available() ? s_.read() : -1; }
  bool tx_status() override { return digitalRead(pin_) == HIGH; }

 private:
  HardwareSerial& s_;
  int pin_;
};

class NvsSeqStore : public stz::SeqStore {
 public:
  void begin() { prefs_.begin("stozher", false); }
  Result load(uint8_t key_id, uint16_t& next) override {
    char k[8];
    name(key_id, k);
    if (!prefs_.isKey(k)) return Result::Missing;
    next = prefs_.getUShort(k, 0);
    return Result::Ok;
  }
  bool save(uint8_t key_id, uint16_t next) override {
    char k[8];
    name(key_id, k);
    return prefs_.putUShort(k, next) == sizeof(uint16_t);
  }
  Preferences& prefs() { return prefs_; }

 private:
  static void name(uint8_t key_id, char* out) { snprintf(out, 8, "seq%u", (unsigned)(key_id & 15)); }
  Preferences prefs_;
};

class UartMesh : public stz::mesh::Port {
 public:
  void begin() { Serial0.begin(STZ_MESH_BAUD, SERIAL_8N1); }  // UART0 on its default pins D6 (TX) and D7 (RX)
  size_t write(const uint8_t* d, size_t n) override { return Serial0.write(d, n); }
  int read() override { return Serial0.available() ? Serial0.read() : -1; }
};

UartKim1 kim1_port(Serial2, STZ_KIM1_STATUS);
UartMesh mesh_port;
stz::mesh::Link* mesh_link = nullptr;
stz::CloudKey cloud_keys[1];
NvsSeqStore seq_store;
stz::ubx::Reader reader;
stz::EpochBuilder builder;
stz::Node* node = nullptr;
stz::Tx* tx = nullptr;

}  // namespace

void setup() {
  Serial.begin(115200);
  Serial1.begin(9600, SERIAL_8N1, STZ_GNSS_RX, STZ_GNSS_TX);
  kim1_port.begin();
  seq_store.begin();

  uint8_t master[16];
  uint32_t node_id = seq_store.prefs().getUInt("node_id", 0);
  if (node_id == 0 || seq_store.prefs().getBytes("master", master, 16) != 16) {
    node_id = 1;
    for (int i = 0; i < 16; i++) master[i] = (uint8_t)i;
    Serial.println(F("WARNING: no provisioning in NVS, using the PUBLIC TEST KEY (node 1). Not a real node."));
  }
  stz::TxConfig tc;
  tc.node_id = node_id;
  tc.key_id = 0;
  stz::Keys keys;
  stz::derive_keys(master, node_id, tc.key_id, keys);
  tx = new stz::Tx(tc, keys, kim1_port, seq_store);
  // The cloud's public key (public by nature); the test key's public half until the real one is provisioned.
  cloud_keys[0].id = 1;
  size_t kn = 0;
  stz::from_hex("03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8", 64, cloud_keys[0].pub, 32, kn);
  mesh_port.begin();
  mesh_link = new stz::mesh::Link(stz::mesh::MeshConfig{}, mesh_port, cloud_keys, 1);
  node = new stz::Node(stz::DetectConfig{}, *tx, mesh_link);

  // The receiver has no flash: configure it at every boot.
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
    if (v.changed && !v.blanked)
      Serial.printf("class %u (rules 0x%02x, confidence %u): %s\n", v.event_class, v.rules, v.confidence,
                    node->last_submit() == stz::SubmitResult::Queued ? "queued" : "NOT queued");
  }
  node->tick(now);
}
