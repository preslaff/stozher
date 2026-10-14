// The node's uplink: sequence numbers, a small queue, the retransmission policy, the daily budget, the KIM1
// exchange and the blanking signal for the detector. Nothing here blocks: call poll() from the main loop.
//
// Rules from docs/packet.md and the specification:
//  - a sequence number is persisted BEFORE it is used and is never reused under one key id;
//  - a confirmed event goes out at once and repeats (default two more times, spread over time);
//  - a status change goes out once, a heartbeat about hourly, and unacknowledged events ride along with the
//    next heartbeat a limited number of times;
//  - a rate limiter keeps the daily total within the Kinéis allowance, events before heartbeats;
//  - +TX_INFO only says the LOCAL transmission ended; delivery is known only from a signed acknowledgement.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "stz_ack.h"
#include "stz_codec.h"
#include "stz_kim1.h"

#ifndef STZ_TX_QUEUE
#define STZ_TX_QUEUE 8  // entries; 4 is enough on the Leonardo
#endif

namespace stz {

// Non-volatile sequence storage (NVS on the ESP32, EEPROM on AVR). The next sequence number to hand out
// is stored per key id; 0 means the key id is used up.
class SeqStore {
 public:
  virtual ~SeqStore() {}
  enum class Result : uint8_t { Ok, Missing, Error };
  virtual Result load(uint8_t key_id, uint16_t& next) = 0;
  virtual bool save(uint8_t key_id, uint16_t next) = 0;
};

enum class TxKind : uint8_t { Event = 0, Status = 1, Heartbeat = 2 };  // value = priority, lowest first

enum class SubmitResult : uint8_t { Queued, QueueFull, SeqStoreFailed, KeyExhausted, EncodeFailed };
enum class AckResult : uint8_t { Applied, NotForThisNode, WrongKey, Stale };

struct TxConfig {
  uint32_t node_id = 0;
  uint8_t key_id = 0;
  kim1::Params radio;
  uint8_t event_repeats = 2;          // extra sends of a confirmed event
  uint32_t repeat_gap_s = 900;        // between repeats: different satellite passes
  uint32_t retry_backoff_s = 60;      // after a failed or timed-out transmission
  uint8_t max_failures = 5;           // per queue entry, then it is left for the next heartbeat
  uint32_t heartbeat_interval_s = 3600;
  uint8_t max_resends = 3;            // heartbeat-time retransmissions of an unacknowledged entry
  uint16_t daily_budget = 48;         // transmissions in any 24 hours (about 1.5 KB)
  uint16_t heartbeat_reserve = 12;    // heartbeats stop this far below the budget; events may use it
  uint32_t min_gap_ms = 5000;         // between two AT+TX commands
  uint32_t tx_timeout_ms = 20000;     // no +TX_INFO by then counts as a failure
  uint32_t blank_guard_ms = 3000;     // detector stays blanked this long after TX_STATUS falls
};

class Tx {
 public:
  Tx(const TxConfig& cfg, const Keys& keys, kim1::Port& port, SeqStore& store);

  // Assigns the next sequence number (persisting it first), fills `r.seq` and `r.key_id`, encodes and queues.
  SubmitResult submit(TxKind kind, Record& r, uint32_t now_ms);

  // Reads the modem, finishes or times out the transmission in flight, starts the next due one.
  void poll(uint32_t now_ms);

  // Detector input: true while our own transmitter may disturb the receiver (command in flight, TX_STATUS
  // high, guard interval after it, or after a mesh transmission).
  bool blanked(uint32_t now_ms);
  void note_mesh_tx(uint32_t now_ms, uint32_t duration_ms);

  // A cloud acknowledgement that has ALREADY been verified (stz_ack). Freshness is checked here.
  AckResult on_ack(const Ack& a);

  bool heartbeat_due(uint32_t now_ms) const;

  // For tests and the status display.
  size_t pending() const;
  uint16_t sent_last_24h() const;
  bool in_flight() const { return state_ == State::AwaitingResult; }
  uint32_t transmissions() const { return total_sent_; }

 private:
  struct Entry {
    bool used = false;
    uint8_t raw[kMaxLen];
    uint8_t len = 0;
    uint16_t seq = 0;
    TxKind kind = TxKind::Heartbeat;
    uint8_t sent = 0;       // completed local transmissions in this burst
    uint8_t fails = 0;
    uint8_t resends = 0;
    uint32_t due_ms = 0;
  };
  enum class State : uint8_t { Idle, AwaitingResult };

  bool alloc_seq(uint16_t& out, SubmitResult& why);
  Entry* free_slot(TxKind incoming);
  uint8_t planned(const Entry& e) const;
  bool budget_allows(TxKind k, uint32_t now_ms);
  void advance_buckets(uint32_t now_ms);
  void start_next(uint32_t now_ms);
  void finish(bool ok, uint32_t now_ms);
  void queue_resends(uint32_t now_ms);
  void observe(uint32_t now_ms);

  TxConfig cfg_;
  Keys keys_;
  kim1::Port& port_;
  SeqStore& store_;
  kim1::LineParser parser_;
  Entry q_[STZ_TX_QUEUE];

  State state_ = State::Idle;
  Entry* current_ = nullptr;
  uint32_t started_ms_ = 0;
  uint32_t last_start_ms_ = 0;
  bool have_started_ = false;
  bool prev_status_ = false;
  uint32_t guard_until_ms_ = 0;
  bool guard_active_ = false;
  uint32_t mesh_until_ms_ = 0;
  bool mesh_active_ = false;
  uint32_t last_heartbeat_ms_ = 0;
  bool have_heartbeat_ = false;
  uint32_t total_sent_ = 0;

  uint8_t bucket_[24];
  uint32_t bucket_start_ms_ = 0;
  uint8_t bucket_idx_ = 0;
  bool buckets_started_ = false;

  bool have_ack_ = false;
  uint16_t last_ack_seq_ = 0;
  uint32_t last_ack_at_ = 0;
};

}  // namespace stz
