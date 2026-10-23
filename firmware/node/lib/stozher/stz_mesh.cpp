#include "stz_mesh.h"

#include <string.h>

namespace stz {
namespace mesh {

size_t build_text(const uint8_t* record, size_t len, char* out, size_t cap) {
  size_t need = 5 + 2 * len + 1;
  if (cap < need) return 0;
  memcpy(out, "STZ1:", 5);
  static const char hex[] = "0123456789abcdef";
  size_t o = 5;
  for (size_t i = 0; i < len; i++) {
    out[o++] = hex[record[i] >> 4];
    out[o++] = hex[record[i] & 15];
  }
  out[o++] = '\n';
  return o;
}

bool Link::keeps_for_ack() const {
#ifdef STZ_NO_ACK_VERIFY
  return false;
#else
  return keys_ != nullptr && n_keys_ > 0;
#endif
}

void Link::on_record(TxKind kind, uint16_t seq, const uint8_t* raw, size_t len, uint32_t now_ms) {
  if (len == 0 || len > kMaxLen) return;
  for (Slot& s : q_)  // the cloud has given these up (kAckWindow)
    if (s.used && beyond_ack_window(s.seq, seq)) s.used = false;
  if (kind == TxKind::Heartbeat)  // a heartbeat is the moment to try the unacknowledged ones again
    for (Slot& s : q_)
      if (s.used && s.waiting && s.resends < cfg_.max_resends) {
        s.waiting = false;
        s.resends++;
        s.sent = (uint8_t)(planned(s) - 1);  // exactly one more send
        s.due_ms = now_ms;
      }
  Slot* slot = nullptr;
  for (Slot& s : q_)
    if (!s.used) { slot = &s; break; }
  if (!slot) {  // full: a record already sent and waiting for an ack goes first (lowest priority, then oldest), so a
                // node whose acks never arrive still sends every new record at least once
    for (Slot& s : q_)
      if (s.waiting && (!slot || (uint8_t)s.kind > (uint8_t)slot->kind || (s.kind == slot->kind && s.seq < slot->seq))) slot = &s;
  }
  if (!slot) {  // full of unsent records: drop the lowest-priority, then the oldest
    for (Slot& s : q_)
      if (!slot || (uint8_t)s.kind > (uint8_t)slot->kind || (s.kind == slot->kind && s.seq < slot->seq)) slot = &s;
    if ((uint8_t)slot->kind < (uint8_t)kind) return;  // everything queued matters more than this
  }
  slot->used = true;
  memcpy(slot->raw, raw, len);
  slot->len = (uint8_t)len;
  slot->seq = seq;
  slot->kind = kind;
  slot->sent = 0;
  slot->resends = 0;
  slot->waiting = false;
  slot->due_ms = now_ms;
}

void Link::on_ack(const Ack& a) {
  for (Slot& s : q_) {
    if (!s.used) continue;
    bool acked = s.seq <= a.max_seq;
    if (!acked && s.seq >= (uint32_t)a.max_seq + 2) {
      uint32_t off = s.seq - ((uint32_t)a.max_seq + 2);
      acked = off < 16 && ((a.mask >> off) & 1);
    }
    if (acked) s.used = false;
  }
}

size_t Link::pending() const {
  size_t n = 0;
  for (const Slot& s : q_)
    if (s.used) n++;
  return n;
}

bool Link::read_ack(PollResult& out) {
#ifdef STZ_NO_ACK_VERIFY
  (void)out;
  for (int i = 0; i < 64 && port_.read() >= 0; i++) {}  // this board cannot verify: drain and ignore
  return false;
#else
  for (int i = 0; i < 256; i++) {
    int c = port_.read();
    if (c < 0) break;
    if (c == '\n' || c == '\r') {
      bool ok = false;
      if (line_len_ > 0 && !overflow_ && keys_ && n_keys_ > 0) {
        Ack a;
        if (verify_ack_text(line_, line_len_, keys_, n_keys_, a) == AckStatus::Ok) {
          out.ack = true;
          out.ack_data = a;
          ok = true;
        }
      }
      line_len_ = 0;
      overflow_ = false;
      if (ok) return true;  // one at a time; the rest stays in the port for the next poll
    } else if (line_len_ < kAckTextMax) {
      line_[line_len_++] = (char)c;
    } else {
      overflow_ = true;
    }
  }
  return false;
#endif
}

PollResult Link::poll(uint32_t now_ms) {
  PollResult r;
  read_ack(r);

  if (!window_started_ || (uint32_t)(now_ms - window_start_ms_) >= 3600000u) {
    window_started_ = true;
    window_start_ms_ = now_ms;
    window_count_ = 0;
  }
  if (have_sent_ && (uint32_t)(now_ms - last_send_ms_) < cfg_.min_gap_ms) return r;
  if (window_count_ >= cfg_.hourly_cap) return r;

  Slot* best = nullptr;
  for (Slot& s : q_) {
    if (!s.used || s.waiting || (int32_t)(now_ms - s.due_ms) < 0) continue;
    if (!best || (uint8_t)s.kind < (uint8_t)best->kind || (s.kind == best->kind && s.seq < best->seq)) best = &s;
  }
  if (!best) return r;

  char text[5 + 2 * kMaxLen + 1];
  size_t n = build_text(best->raw, best->len, text, sizeof text);
  if (n == 0 || port_.write((const uint8_t*)text, n) != n) {
    best->due_ms = now_ms + 1000;  // try again shortly; never block
    return r;
  }
  have_sent_ = true;
  last_send_ms_ = now_ms;
  window_count_++;
  r.sent = true;
  if (++best->sent >= planned(*best)) {
    if (best->kind != TxKind::Heartbeat && keeps_for_ack()) best->waiting = true;  // until an ack covers it
    else best->used = false;
  } else {
    best->due_ms = now_ms + cfg_.repeat_gap_s * 1000u;
  }
  return r;
}

}  // namespace mesh
}  // namespace stz
