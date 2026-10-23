// Store and forward on a gateway (docs/mesh.md, "Store and forward on the gateway"): every witness record the gateway
// hears is kept in its own flash and handed to whichever bridge connects next, until a signed ack shows the backend has
// it. Records are kept as the opaque signed bytes: the gateway holds no node keys and no signing key, so it cannot read,
// change or invent a record. A copy delivered twice (two kits, or live and again from the store) is a `duplicate`.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "stz_ack.h"
#include "stz_codec.h"

#ifndef STZ_STORE_SLOTS
#define STZ_STORE_SLOTS 1024  // 48 bytes each in flash, 8 bytes each in RAM
#endif
#ifndef STZ_STORE_SENDERS
#define STZ_STORE_SENDERS 32
#endif
#ifndef STZ_STORE_QUOTA
#define STZ_STORE_QUOTA (STZ_STORE_SLOTS / 4)  // per sender, so one noisy channel member cannot evict the others
#endif

namespace stz {
namespace store {

constexpr size_t kSlotBytes = 48;

// Fixed-size slots on some medium (a file on the gateway, RAM on the host). A slot that reads back damaged is empty.
class Backend {
 public:
  virtual ~Backend() {}
  virtual bool read(size_t slot, uint8_t out[kSlotBytes]) = 0;
  virtual bool write(size_t slot, const uint8_t in[kSlotBytes]) = 0;
};

struct Entry {
  uint32_t from = 0;     // the sender's mesh number (the record's transport id)
  uint32_t rx_time = 0;  // Unix seconds when the gateway heard it, 0 if the gateway had no valid time
  uint32_t order = 0;    // arrival order, never reused while the store exists
  uint8_t raw[kMaxLen];
  uint8_t len = 0;
};

enum class AddResult : uint8_t { Stored, Duplicate, Invalid, NoRoom };

struct Counts {
  uint32_t held = 0;
  uint32_t senders = 0;
  uint32_t stored = 0;          // since boot
  uint32_t duplicates = 0;
  uint32_t evicted = 0;         // pushed out by the quota or a full store
  uint32_t deleted_by_ack = 0;
  uint32_t expired = 0;
};

// "STZ1:" + 56 or 64 hex characters (a trailing newline is allowed), record version 1. Writes the raw bytes.
bool parse_text(const char* text, size_t n, uint8_t raw[kMaxLen], size_t& len);

class Store {
 public:
  explicit Store(Backend& b) : b_(b) {}

  // Reads every slot back (after a reboot) and rebuilds the index. Damaged slots are cleared.
  void begin();

  // A record heard from `from`. The same sender, key id and sequence number again is a mesh repeat (`Duplicate`). When the
  // sender is at its quota, its own oldest heartbeat gives way, else its oldest record; when the whole store is full, the
  // same happens to the sender holding the most.
  AddResult add(uint32_t from, uint32_t rx_time, const uint8_t* raw, size_t len);

  // The stored record that arrived next after `after_order` (0 = from the start). False when there is none.
  bool next(uint32_t after_order, Entry& out);

  // A verified ack sent to the node `dest`: deletes the stored records from `dest` it shows the backend has. Returns the
  // number deleted. Records the cloud has given up (17 or more behind its newest) are kept: the store may still fill
  // the gap, so they go only by age or room.
  size_t apply_ack(uint32_t dest, const Ack& a);
  // The same from the `STZA1:` text, verified with `keys` first; -1 if it does not verify (nothing changes).
  int apply_ack_text(uint32_t dest, const char* text, size_t n, const CloudKey* keys, size_t n_keys);

  // Deletes records heard more than `max_age_s` before `now` (both Unix seconds; records with no time are kept).
  size_t expire(uint32_t now, uint32_t max_age_s);

  size_t held_from(uint32_t from) const;
  const Counts& counts() const { return c_; }

 private:
  struct Meta {  // the RAM index, one per slot
    uint32_t order = 0;  // 0 = empty
    uint16_t seq = 0;
    uint8_t sender = 0;  // index into senders_
    uint8_t flags = 0;   // bit 0 heartbeat, bits 4-7 key id
  };
  int sender_index(uint32_t from, bool create);
  bool read_entry(size_t slot, Entry& e);
  void clear(size_t slot);
  int pick_victim(int sender) const;  // -1 = none

  Backend& b_;
  Meta m_[STZ_STORE_SLOTS];
  uint32_t senders_[STZ_STORE_SENDERS] = {};
  uint16_t per_sender_[STZ_STORE_SENDERS] = {};
  uint32_t next_order_ = 1;
  Counts c_;
};

}  // namespace store
}  // namespace stz
