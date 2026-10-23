#include "stz_tx.h"

#include <string.h>

namespace stz {

namespace {
bool reached(uint32_t now, uint32_t t) { return (int32_t)(now - t) >= 0; }
constexpr uint32_t kHourMs = 3600000u;
}  // namespace

Tx::Tx(const TxConfig& cfg, const Keys& keys, kim1::Port& port, SeqStore& store)
    : cfg_(cfg), keys_(keys), port_(port), store_(store) {
  memset(bucket_, 0, sizeof bucket_);
}

uint8_t Tx::planned(const Entry& e) const { return e.kind == TxKind::Event ? (uint8_t)(1 + cfg_.event_repeats) : 1; }

bool Tx::alloc_seq(uint16_t& out, SubmitResult& why) {
  uint16_t next = 0;
  SeqStore::Result r = store_.load(cfg_.key_id, next);
  if (r == SeqStore::Result::Error) { why = SubmitResult::SeqStoreFailed; return false; }
  if (r == SeqStore::Result::Missing) next = 1;
  else if (next == 0) { why = SubmitResult::KeyExhausted; return false; }
  uint16_t after = next == 65535 ? 0 : (uint16_t)(next + 1);
  // Persist first, and read it back: a number is only used once the store holds the one after it.
  uint16_t check = 0xFFFF;
  if (!store_.save(cfg_.key_id, after) || store_.load(cfg_.key_id, check) != SeqStore::Result::Ok || check != after) {
    why = SubmitResult::SeqStoreFailed;
    return false;
  }
  out = next;
  return true;
}

Tx::Entry* Tx::free_slot(TxKind incoming) {
  for (Entry& e : q_)
    if (!e.used) return &e;
  Entry* victim = nullptr;
  for (Entry& e : q_)  // 1. entries that are done sending and still wait for an acknowledgement, oldest first
    if (e.used && e.sent >= planned(e) && &e != current_ && (!victim || e.seq < victim->seq)) victim = &e;
  if (!victim && incoming != TxKind::Heartbeat) {
    for (Entry& e : q_)  // 2. a new event may displace the oldest waiting heartbeat
      if (e.used && e.kind == TxKind::Heartbeat && &e != current_ && (!victim || e.seq < victim->seq)) victim = &e;
  }
  if (victim) victim->used = false;
  return victim;
}

#ifndef STZ_NO_ACK_HORIZON
void Tx::drop_given_up(uint16_t newest) {
  for (Entry& e : q_)  // the cloud has given these up (kAckWindow): so does the node, on every record as the mesh queue does
    if (e.used && &e != current_ && beyond_ack_window(e.seq, newest)) e.used = false;
}
#endif

void Tx::queue_resends(uint32_t now_ms) {
  for (Entry& e : q_) {
    if (!e.used || &e == current_) continue;
    if (e.kind == TxKind::Heartbeat || e.sent < planned(e) || e.resends >= cfg_.max_resends) continue;
    e.resends++;
    e.sent = (uint8_t)(planned(e) - 1);  // exactly one more send
    e.fails = 0;
    e.due_ms = now_ms;
  }
}

SubmitResult Tx::submit(TxKind kind, Record& r, uint32_t now_ms) {
  Entry* slot = nullptr;
  if (cfg_.satellite_enabled) {
    slot = free_slot(kind);  // before the sequence number: a full queue must not burn one
    if (!slot) return SubmitResult::QueueFull;
  }
  uint16_t seq;
  SubmitResult why;
  if (!alloc_seq(seq, why)) return why;
  r.seq = seq;
  r.key_id = cfg_.key_id;
  Entry e;
  e.len = (uint8_t)encode(r, cfg_.node_id, keys_, e.raw);
  if (e.len == 0) return SubmitResult::EncodeFailed;
  e.used = true;
  e.seq = seq;
  e.kind = kind;
  e.due_ms = now_ms;
  if (slot) *slot = e;
#ifndef STZ_NO_ACK_HORIZON
  drop_given_up(seq);
#endif
  if (kind == TxKind::Heartbeat) {
    last_heartbeat_ms_ = now_ms;
    have_heartbeat_ = true;
    queue_resends(now_ms);
  }
  if (sink_) sink_->on_record(kind, seq, e.raw, e.len, now_ms);
  return SubmitResult::Queued;
}

bool Tx::heartbeat_due(uint32_t now_ms) const {
  return !have_heartbeat_ || (uint32_t)(now_ms - last_heartbeat_ms_) >= cfg_.heartbeat_interval_s * 1000u;
}

void Tx::advance_buckets(uint32_t now_ms) {
  if (!buckets_started_) {
    buckets_started_ = true;
    bucket_start_ms_ = now_ms;
    return;
  }
  int guard = 0;
  while ((uint32_t)(now_ms - bucket_start_ms_) >= kHourMs) {
    bucket_start_ms_ += kHourMs;
    bucket_idx_ = (uint8_t)((bucket_idx_ + 1) % 24);
    bucket_[bucket_idx_] = 0;
    if (++guard > 24) {  // a very long gap: everything has aged out
      memset(bucket_, 0, sizeof bucket_);
      bucket_start_ms_ = now_ms;
      break;
    }
  }
}

uint16_t Tx::sent_last_24h() const {
  uint16_t total = 0;
  for (uint8_t b : bucket_) total = (uint16_t)(total + b);
  return total;
}

bool Tx::budget_allows(TxKind k, uint32_t) {
  uint16_t limit = cfg_.daily_budget;
  if (k == TxKind::Heartbeat) limit = limit > cfg_.heartbeat_reserve ? (uint16_t)(limit - cfg_.heartbeat_reserve) : 0;
  return sent_last_24h() < limit;
}

void Tx::start_next(uint32_t now_ms) {
  if (have_started_ && !reached(now_ms, last_start_ms_ + cfg_.min_gap_ms)) return;
  if (port_.tx_status()) return;  // the module says it is busy
  Entry* best = nullptr;
  for (Entry& e : q_) {
    if (!e.used || e.sent >= planned(e) || !reached(now_ms, e.due_ms)) continue;
    if (!budget_allows(e.kind, now_ms)) continue;
    if (!best || (uint8_t)e.kind < (uint8_t)best->kind || (e.kind == best->kind && e.seq < best->seq)) best = &e;
  }
  if (!best) return;

  char cmd[kim1::kMaxCommand];
  size_t n = 0;
  if (kim1::build_tx(cfg_.radio, best->raw, best->len, cmd, sizeof cmd, n) != kim1::CmdStatus::Ok) {
    best->fails = cfg_.max_failures;  // a configuration error will not fix itself by retrying
    best->sent = planned(*best);
    if (best->kind == TxKind::Heartbeat) best->used = false;
    return;
  }
  have_started_ = true;
  last_start_ms_ = now_ms;
  if (port_.write((const uint8_t*)cmd, n) != n) {
    best->fails++;
    best->due_ms = now_ms + cfg_.retry_backoff_s * 1000u;
    return;
  }
  state_ = State::AwaitingResult;
  current_ = best;
  started_ms_ = now_ms;
  uint8_t& b = bucket_[bucket_idx_];
  if (b < 255) b++;
}

void Tx::finish(bool ok, uint32_t now_ms) {
  Entry* e = current_;
  state_ = State::Idle;
  current_ = nullptr;
  guard_active_ = true;
  guard_until_ms_ = now_ms + cfg_.blank_guard_ms;
  total_sent_++;
  if (!e || !e->used) return;  // acknowledged while it was on the air
  if (ok) {
    e->fails = 0;
    e->sent++;
    if (e->kind == TxKind::Heartbeat) e->used = false;  // nothing to wait for
    else if (e->sent < planned(*e)) e->due_ms = now_ms + cfg_.repeat_gap_s * 1000u;
  } else {
    e->fails++;
    if (e->fails >= cfg_.max_failures) {
      e->sent = planned(*e);  // give up this burst; a later heartbeat may offer it again
      if (e->kind == TxKind::Heartbeat) e->used = false;
    } else {
      e->due_ms = now_ms + cfg_.retry_backoff_s * 1000u;
    }
  }
}

void Tx::observe(uint32_t now_ms) {
  bool st = port_.tx_status();
  if (prev_status_ && !st) {
    guard_active_ = true;
    guard_until_ms_ = now_ms + cfg_.blank_guard_ms;
  }
  prev_status_ = st;
}

void Tx::poll(uint32_t now_ms) {
  if (!cfg_.satellite_enabled) return;
  for (int i = 0; i < 64; i++) {  // bounded: a chatty or broken modem cannot keep us here
    int c = port_.read();
    if (c < 0) break;
    kim1::Line l = parser_.feed((uint8_t)c);
    if (state_ == State::AwaitingResult && l == kim1::Line::TxDone) finish(true, now_ms);
    else if (state_ == State::AwaitingResult && l == kim1::Line::TxFail) finish(false, now_ms);
  }
  observe(now_ms);
  advance_buckets(now_ms);
  if (state_ == State::AwaitingResult && (uint32_t)(now_ms - started_ms_) >= cfg_.tx_timeout_ms) finish(false, now_ms);
  if (state_ == State::Idle) start_next(now_ms);
}

bool Tx::blanked(uint32_t now_ms) {
  if (cfg_.satellite_enabled) {
    observe(now_ms);
    if (state_ == State::AwaitingResult || port_.tx_status()) return true;
  }
  if (guard_active_) {
    if (!reached(now_ms, guard_until_ms_)) return true;
    guard_active_ = false;
  }
  if (mesh_active_) {
    if (!reached(now_ms, mesh_until_ms_)) return true;
    mesh_active_ = false;
  }
  return false;
}

void Tx::note_mesh_tx(uint32_t now_ms, uint32_t duration_ms) {
  mesh_active_ = true;
  mesh_until_ms_ = now_ms + duration_ms + cfg_.blank_guard_ms;
}

AckResult Tx::on_ack(const Ack& a) {
  if (a.node_id != cfg_.node_id) return AckResult::NotForThisNode;
  if (a.key_id != cfg_.key_id) return AckResult::WrongKey;
  if (have_ack_ && (a.signed_at < last_ack_at_ || a.max_seq < last_ack_seq_)) return AckResult::Stale;
  have_ack_ = true;
  last_ack_at_ = a.signed_at;
  last_ack_seq_ = a.max_seq;
  for (Entry& e : q_) {
    if (!e.used) continue;
    bool acked = e.seq <= a.max_seq;
    if (!acked && e.seq >= (uint32_t)a.max_seq + 2) {
      uint32_t off = e.seq - ((uint32_t)a.max_seq + 2);
      acked = off < 16 && ((a.mask >> off) & 1);
    }
    if (acked) e.used = false;
  }
  acks_applied_++;
  if (sink_) sink_->on_ack(a);
  return AckResult::Applied;
}

size_t Tx::pending() const {
  size_t n = 0;
  for (const Entry& e : q_)
    if (e.used) n++;
  return n;
}

}  // namespace stz
