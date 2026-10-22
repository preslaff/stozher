// Mesh uplink: the same record, as `STZ1:` + hex text, to a stock Meshtastic node whose Serial module is in
// TEXTMSG mode (docs/mesh.md), and the signed acknowledgements coming back as `STZA1:` text lines.
// Optional: the node works without it, and it never blocks. LoRa airtime is shared, so sends are spaced and capped.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "stz_ack.h"
#include "stz_tx.h"

#ifndef STZ_MESH_QUEUE
#define STZ_MESH_QUEUE 4
#endif
static_assert(STZ_MESH_QUEUE <= 16, "STZ_MESH_QUEUE must stay within the ack window");

namespace stz {
namespace mesh {

class Port {
 public:
  virtual ~Port() {}
  virtual size_t write(const uint8_t* data, size_t len) = 0;
  virtual int read() = 0;  // -1 when nothing is waiting
};

// "STZ1:" + lowercase hex + "\n" (the Serial module sends a message per line). Returns the length, 0 if `cap` is too
// small. Must equal "STZ1:" + the golden vector hex (tests/vectors/witness_records.json).
size_t build_text(const uint8_t* record, size_t len, char* out, size_t cap);

struct MeshConfig {
  uint8_t event_sends = 2;        // an event is sent, then once more after repeat_gap_s
  uint32_t repeat_gap_s = 30;
  uint32_t min_gap_ms = 5000;     // between two messages: shared airtime
  uint16_t hourly_cap = 40;
  uint32_t airtime_ms = 2000;     // how long one message disturbs our own receiver (told to the blanking logic)
};

struct PollResult {
  bool sent = false;       // a message went to the Serial module in this poll
  bool ack = false;        // a verified acknowledgement arrived
  Ack ack_data;
};

class Link : public RecordSink {
 public:
  // `keys`: the cloud public keys this node trusts; with none (or on a board that cannot verify) acks are ignored.
  Link(const MeshConfig& cfg, Port& port, const CloudKey* keys, size_t n_keys)
      : cfg_(cfg), port_(port), keys_(keys), n_keys_(n_keys) {}

  void on_record(TxKind kind, uint16_t seq, const uint8_t* raw, size_t len, uint32_t now_ms) override;
  void on_ack(const Ack& a) override;
  PollResult poll(uint32_t now_ms);

  size_t pending() const;
  const MeshConfig& config() const { return cfg_; }

 private:
  struct Slot {
    bool used = false;
    uint8_t raw[kMaxLen];
    uint8_t len = 0;
    uint16_t seq = 0;
    TxKind kind = TxKind::Heartbeat;
    uint8_t sent = 0;
    uint32_t due_ms = 0;
  };
  uint8_t planned(const Slot& s) const { return s.kind == TxKind::Event ? cfg_.event_sends : 1; }
  bool read_ack(PollResult& out);

  MeshConfig cfg_;
  Port& port_;
  const CloudKey* keys_;
  size_t n_keys_;
  Slot q_[STZ_MESH_QUEUE];
  uint32_t last_send_ms_ = 0;
  bool have_sent_ = false;
  uint32_t window_start_ms_ = 0;
  uint16_t window_count_ = 0;
  bool window_started_ = false;
#ifndef STZ_NO_ACK_VERIFY
  char line_[kAckTextMax + 1];
  size_t line_len_ = 0;
  bool overflow_ = false;
#endif
};

}  // namespace mesh
}  // namespace stz
