#include "stz_store.h"

#include <string.h>

namespace stz {
namespace store {

// Slot layout: marker, length, sender (LE), receive time (LE), order (LE), 32 record bytes, CRC-16 of all before it.
// An all-zero slot is empty; a slot whose marker or CRC is wrong (a write cut by a power loss) counts as empty.
namespace {
constexpr uint8_t kMarker = 0x5A;
constexpr size_t kRawAt = 14, kCrcAt = 46;

uint16_t crc16(const uint8_t* d, size_t n) {  // CRC-16/CCITT-FALSE
  uint16_t c = 0xFFFF;
  for (size_t i = 0; i < n; i++) {
    c ^= (uint16_t)d[i] << 8;
    for (int b = 0; b < 8; b++) c = (c & 0x8000) ? (uint16_t)((c << 1) ^ 0x1021) : (uint16_t)(c << 1);
  }
  return c;
}
void put32(uint8_t* p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24); }
uint32_t get32(const uint8_t* p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }

bool valid_record(const uint8_t* raw, size_t len) {
  if ((raw[0] >> 4) != 1) return false;
  bool extended = raw[3] & 0x08;
  return len == (extended ? kExtLen : kCoreLen);
}
uint16_t seq_of(const uint8_t* raw) { return (uint16_t)(raw[1] << 8 | raw[2]); }
uint8_t flags_of(const uint8_t* raw) { return (uint8_t)((raw[3] & 0xF0) | ((raw[0] & 0x0F) == 0 ? 1 : 0)); }
}  // namespace

bool parse_text(const char* text, size_t n, uint8_t raw[kMaxLen], size_t& len) {
  while (n > 0 && (text[n - 1] == '\n' || text[n - 1] == '\r')) n--;
  len = 0;
  if (n < 5 || memcmp(text, "STZ1:", 5) != 0) return false;
  if (n - 5 != 2 * kCoreLen && n - 5 != 2 * kExtLen) return false;
  size_t got = 0;
  if (from_hex(text + 5, n - 5, raw, kMaxLen, got) != Status::Ok || !valid_record(raw, got)) return false;
  len = got;
  return true;
}

void Store::begin() {
  memset(senders_, 0, sizeof senders_);
  memset(per_sender_, 0, sizeof per_sender_);
  next_order_ = 1;
  c_ = Counts();
  for (size_t i = 0; i < STZ_STORE_SLOTS; i++) {
    m_[i] = Meta();
    uint8_t s[kSlotBytes];
    if (!b_.read(i, s)) continue;
    bool empty = true;
    for (uint8_t x : s)
      if (x) { empty = false; break; }
    if (empty) continue;
    uint8_t len = s[1];
    bool ok = s[0] == kMarker && (len == kCoreLen || len == kExtLen) && crc16(s, kCrcAt) == (uint16_t)(s[kCrcAt] | s[kCrcAt + 1] << 8) &&
              get32(s + 10) != 0 && valid_record(s + kRawAt, len);
    int who = ok ? sender_index(get32(s + 2), true) : -1;
    if (who < 0) {
      clear(i);
      continue;
    }
    m_[i].order = get32(s + 10);
    m_[i].rx_time = get32(s + 6);
    m_[i].seq = seq_of(s + kRawAt);
    m_[i].sender = (uint8_t)who;
    m_[i].flags = flags_of(s + kRawAt);
    per_sender_[who]++;
    c_.held++;
    if (m_[i].order >= next_order_) next_order_ = m_[i].order + 1;
  }
  for (size_t i = 0; i < STZ_STORE_SENDERS; i++)
    if (per_sender_[i]) c_.senders++;
}

int Store::sender_index(uint32_t from, bool create) {
  int free_at = -1;
  for (int i = 0; i < STZ_STORE_SENDERS; i++) {
    if (per_sender_[i] && senders_[i] == from) return i;
    if (!per_sender_[i] && free_at < 0) free_at = i;
  }
  if (!create || free_at < 0 || from == 0) return -1;
  senders_[free_at] = from;
  return free_at;
}

void Store::clear(size_t slot) {
  uint8_t z[kSlotBytes] = {0};
  b_.write(slot, z);
  if (m_[slot].order) {
    per_sender_[m_[slot].sender]--;
    c_.held--;
    if (!per_sender_[m_[slot].sender]) c_.senders--;
  }
  m_[slot] = Meta();
}

bool Store::read_entry(size_t slot, Entry& e) {
  uint8_t s[kSlotBytes];
  if (!m_[slot].order || !b_.read(slot, s) || s[0] != kMarker) return false;
  if (crc16(s, kCrcAt) != (uint16_t)(s[kCrcAt] | s[kCrcAt + 1] << 8)) return false;
  if ((s[1] != kCoreLen && s[1] != kExtLen) || get32(s + 10) != m_[slot].order) return false;  // changed under us
  e.from = get32(s + 2);
  e.rx_time = get32(s + 6);
  e.order = get32(s + 10);
  e.len = s[1];
  memcpy(e.raw, s + kRawAt, e.len);
  return true;
}

int Store::pick_victim(int sender) const {
  int best = -1;
  bool best_hb = false;
  for (size_t i = 0; i < STZ_STORE_SLOTS; i++) {
    const Meta& m = m_[i];
    if (!m.order || (sender >= 0 && m.sender != sender)) continue;
    bool hb = m.flags & 1;
    if (best < 0 || (hb && !best_hb) || (hb == best_hb && m.order < m_[best].order)) {
      best = (int)i;
      best_hb = hb;
    }
  }
  return best;
}

AddResult Store::add(uint32_t from, uint32_t rx_time, const uint8_t* raw, size_t len, bool handed) {
  if (!raw || from == 0 || !valid_record(raw, len)) return AddResult::Invalid;
  int who = sender_index(from, false);
  if (who >= 0) {
    uint16_t seq = seq_of(raw);
    uint8_t key = raw[3] & 0xF0;
    Entry e;
    for (size_t i = 0; i < STZ_STORE_SLOTS; i++)  // the same bytes again: a mesh repeat
      if (m_[i].order && m_[i].sender == who && m_[i].seq == seq && (m_[i].flags & 0xF0) == key && read_entry(i, e) &&
          e.len == len && memcmp(e.raw, raw, len) == 0) {
        if (handed) m_[i].flags |= 2;
        c_.duplicates++;
        return AddResult::Duplicate;
      }
  }
  if (who < 0) who = sender_index(from, true);
  if (who < 0) return AddResult::NoRoom;  // every sender entry is in use

  int slot = -1;
  if (per_sender_[who] >= STZ_STORE_QUOTA) {
    slot = pick_victim(who);
  } else {
    for (size_t i = 0; i < STZ_STORE_SLOTS; i++)
      if (!m_[i].order) { slot = (int)i; break; }
    if (slot < 0) {  // full: the sender holding the most gives way
      int heaviest = who;
      for (int i = 0; i < STZ_STORE_SENDERS; i++)
        if (per_sender_[i] > per_sender_[heaviest]) heaviest = i;
      slot = pick_victim(heaviest);
    }
  }
  if (slot < 0) return AddResult::NoRoom;
  if (m_[slot].order) {
    clear((size_t)slot);
    c_.evicted++;
  }
  if (!per_sender_[who]) senders_[who] = from;  // the entry may have been freed by the eviction just above

  uint8_t s[kSlotBytes] = {0};
  s[0] = kMarker;
  s[1] = (uint8_t)len;
  put32(s + 2, from);
  put32(s + 6, rx_time);
  put32(s + 10, next_order_);
  memcpy(s + kRawAt, raw, len);
  uint16_t c = crc16(s, kCrcAt);
  s[kCrcAt] = (uint8_t)c;
  s[kCrcAt + 1] = (uint8_t)(c >> 8);
  if (!b_.write((size_t)slot, s)) return AddResult::NoRoom;

  Meta& m = m_[slot];
  m.order = next_order_++;
  m.rx_time = rx_time;
  m.seq = seq_of(raw);
  m.sender = (uint8_t)who;
  m.flags = (uint8_t)(flags_of(raw) | (handed ? 2 : 0));
  if (!per_sender_[who]) c_.senders++;
  per_sender_[who]++;
  c_.held++;
  c_.stored++;
  return AddResult::Stored;
}

bool Store::next(uint32_t after_order, Entry& out) {
  for (;;) {
    int best = -1;
    for (size_t i = 0; i < STZ_STORE_SLOTS; i++)
      if (m_[i].order > after_order && (best < 0 || m_[i].order < m_[best].order)) best = (int)i;
    if (best < 0) return false;
    if (read_entry((size_t)best, out)) return true;
    clear((size_t)best);  // unreadable now: drop it and move on
  }
}

void Store::mark_handed(uint32_t order) {
  for (Meta& m : m_)
    if (m.order == order) {
      m.flags |= 2;
      return;
    }
}

size_t Store::apply_ack(uint32_t dest, const Ack& a) {
  int who = sender_index(dest, false);
  if (who < 0) return 0;
  // The cloud's newest record for this node and key: the mask always reaches it (docs/control.md).
  uint32_t newest = a.max_seq;
  for (int bit = 15; bit >= 0; bit--)
    if ((a.mask >> bit) & 1) { newest = (uint32_t)a.max_seq + 2 + (uint32_t)bit; break; }
  // Everything up to max_seq was received *or given up*; only above the give-up point is it known to be received. At or
  // below it, a record a bridge was handed is done with too; one no bridge has seen may fill a given-up gap later.
  uint32_t gave_up_to = newest >= kAckWindow ? newest - kAckWindow : 0;
  size_t n = 0;
  for (size_t i = 0; i < STZ_STORE_SLOTS; i++) {
    Meta& m = m_[i];
    if (!m.order || m.sender != who || (m.flags >> 4) != a.key_id) continue;
    bool have = m.seq <= a.max_seq && (m.seq > gave_up_to || (m.flags & 2));
    if (!have && m.seq >= (uint32_t)a.max_seq + 2) {
      uint32_t off = m.seq - ((uint32_t)a.max_seq + 2);
      have = off < 16 && ((a.mask >> off) & 1);
    }
    if (have) {
      clear(i);
      n++;
    }
  }
  c_.deleted_by_ack += (uint32_t)n;
  return n;
}

int Store::apply_ack_text(uint32_t dest, const char* text, size_t n, const CloudKey* keys, size_t n_keys) {
  Ack a;
  if (!keys || !n_keys || verify_ack_text(text, n, keys, n_keys, a) != AckStatus::Ok) return -1;
  return (int)apply_ack(dest, a);
}

size_t Store::expire(uint32_t now, uint32_t max_age_s) {
  if (now == 0) return 0;
  size_t n = 0;
  for (size_t i = 0; i < STZ_STORE_SLOTS; i++) {
    uint32_t t = m_[i].rx_time;
    if (m_[i].order && t != 0 && t <= now && now - t > max_age_s) {
      clear(i);
      n++;
    }
  }
  c_.expired += (uint32_t)n;
  return n;
}

size_t Store::held_from(uint32_t from) const {
  for (int i = 0; i < STZ_STORE_SENDERS; i++)
    if (per_sender_[i] && senders_[i] == from) return per_sender_[i];
  return 0;
}

}  // namespace store
}  // namespace stz
