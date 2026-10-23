// The KIM1 command and reply, the sequence-number rules, the retransmission policy, the daily budget and the
// blanking signal, against a scripted modem (stz_fake_kim1.h). No hardware involved.
#include <string.h>
#include <unity.h>

#include "stz_codec.h"
#include "stz_detect.h"
#include "stz_fake_kim1.h"
#include "stz_tx.h"

using namespace stz;

static const uint32_t kNode = 1;

static void master_key(uint8_t m[16]) {
  for (int i = 0; i < 16; i++) m[i] = (uint8_t)i;  // the public test key
}

struct Rig {
  FakeKim1 modem;
  FakeStore store;
  Keys keys;
  TxConfig cfg;
  Tx* tx;
  uint32_t now = 100000;
  Rig() {
    uint8_t m[16];
    master_key(m);
    derive_keys(m, kNode, 0, keys);
    cfg.node_id = kNode;
    cfg.key_id = 0;
    tx = new Tx(cfg, keys, modem, store);
  }
  explicit Rig(const TxConfig& c) {
    uint8_t m[16];
    master_key(m);
    derive_keys(m, c.node_id, c.key_id, keys);
    cfg = c;
    tx = new Tx(cfg, keys, modem, store);
  }
  ~Rig() { delete tx; }
  // Advance time by `ms` in 100 ms steps, running the modem and the driver.
  void run(uint32_t ms) {
    for (uint32_t t = 0; t < ms; t += 100) {
      now += 100;
      modem.step(now);
      tx->poll(now);
    }
  }
  SubmitResult send(TxKind kind, uint8_t event_class = kJamming) {
    Record r;
    r.type = kind == TxKind::Heartbeat ? kHeartbeat : kEvent;
    r.event_class = event_class;
    r.lat_e7 = 425000000;
    r.lon_e7 = 270000000;
    r.timestamp = 1795775405u;
    return tx->submit(kind, r, now);
  }
};

// ---- command and reply ----

void test_the_at_tx_command_is_built_exactly() {
  kim1::Params p;
  const uint8_t data[4] = {0x11, 0x00, 0xAB, 0xFF};
  char out[kim1::kMaxCommand];
  size_t n = 0;
  TEST_ASSERT_EQUAL(kim1::CmdStatus::Ok, kim1::build_tx(p, data, 4, out, sizeof out, n));
  out[n] = 0;
  TEST_ASSERT_EQUAL_STRING("AT+TX=B1,300,500,1100ABFF\r\n", out);
}

void test_commands_with_bad_parameters_are_refused() {
  kim1::Params p;
  const uint8_t d[4] = {1, 2, 3, 4};
  char out[kim1::kMaxCommand];
  size_t n;
  p.band[1] = '0';
  TEST_ASSERT_EQUAL(kim1::CmdStatus::BadBand, kim1::build_tx(p, d, 4, out, sizeof out, n));
  p = kim1::Params();
  p.freq = 601;
  TEST_ASSERT_EQUAL(kim1::CmdStatus::BadFreq, kim1::build_tx(p, d, 4, out, sizeof out, n));
  p.band[1] = '4';  // B4 and B5 go up to 700
  p.freq = 700;
  TEST_ASSERT_EQUAL(kim1::CmdStatus::Ok, kim1::build_tx(p, d, 4, out, sizeof out, n));
  p = kim1::Params();
  p.power_mw = 2000;  // "not implemented yet" in the product sheet
  TEST_ASSERT_EQUAL(kim1::CmdStatus::BadPower, kim1::build_tx(p, d, 4, out, sizeof out, n));
  p = kim1::Params();
  TEST_ASSERT_EQUAL(kim1::CmdStatus::BadData, kim1::build_tx(p, d, 0, out, sizeof out, n));
  uint8_t big[33] = {0};
  TEST_ASSERT_EQUAL(kim1::CmdStatus::BadData, kim1::build_tx(p, big, 33, out, sizeof out, n));
  char tiny[10];
  TEST_ASSERT_EQUAL(kim1::CmdStatus::BufferTooSmall, kim1::build_tx(p, d, 4, tiny, sizeof tiny, n));
}

void test_a_full_size_record_fits_the_command_buffer() {
  kim1::Params p;
  uint8_t d[kim1::kMaxDataBytes];
  memset(d, 0xA5, sizeof d);
  char out[kim1::kMaxCommand];
  size_t n = 0;
  TEST_ASSERT_EQUAL(kim1::CmdStatus::Ok, kim1::build_tx(p, d, sizeof d, out, sizeof out, n));
  TEST_ASSERT_TRUE(n <= sizeof out);
}

static kim1::Line feed_line(kim1::LineParser& p, const char* s) {
  kim1::Line last = kim1::Line::None;
  for (; *s; s++) {
    kim1::Line l = p.feed((uint8_t)*s);
    if (l != kim1::Line::None) last = l;
  }
  return last;
}

void test_tx_info_replies_are_understood() {
  kim1::LineParser p;
  TEST_ASSERT_EQUAL(kim1::Line::TxDone, feed_line(p, "+TX_INFO: 1\r\n"));
  TEST_ASSERT_EQUAL(kim1::Line::TxFail, feed_line(p, "+TX_INFO:0\r\n"));
  TEST_ASSERT_EQUAL(kim1::Line::TxDone, feed_line(p, "\r\n+TX_INFO: 1\n"));
  TEST_ASSERT_EQUAL(kim1::Line::Other, feed_line(p, "+TX_INFO: 7\r\n"));
  TEST_ASSERT_EQUAL(kim1::Line::Other, feed_line(p, "OK\r\n"));
  TEST_ASSERT_EQUAL(kim1::Line::None, feed_line(p, "+TX_INFO: 1"));  // no line end yet
  TEST_ASSERT_EQUAL(kim1::Line::TxDone, feed_line(p, "\r"));         // now complete
}

void test_long_garbage_cannot_overflow_or_fake_a_reply() {
  kim1::LineParser p;
  char junk[200];
  memset(junk, 'x', sizeof junk - 1);
  junk[sizeof junk - 1] = 0;
  feed_line(p, junk);
  TEST_ASSERT_EQUAL(kim1::Line::Other, feed_line(p, "\r\n"));
  // a long line that merely starts like a reply is not one
  char tricky[200] = "+TX_INFO: 1";
  memset(tricky + 11, ' ', 150);
  tricky[161] = 0;
  TEST_ASSERT_TRUE(feed_line(p, tricky) == kim1::Line::None);
  TEST_ASSERT_EQUAL(kim1::Line::Other, feed_line(p, "\r\n"));
}

// ---- sequence numbers ----

void test_sequence_numbers_start_at_one_and_are_stored_before_use() {
  Rig r;
  TEST_ASSERT_EQUAL(SubmitResult::Queued, r.send(TxKind::Event));
  uint16_t next = 0;
  TEST_ASSERT_EQUAL(SeqStore::Result::Ok, r.store.load(0, next));
  TEST_ASSERT_EQUAL_UINT16(2, next);  // the number after the one now in use is already stored
  Record rec;
  TEST_ASSERT_EQUAL(SubmitResult::Queued, r.tx->submit(TxKind::Event, rec, r.now));
  TEST_ASSERT_EQUAL_UINT16(2, rec.seq);
  TEST_ASSERT_EQUAL(2, r.store.saves);
}

void test_after_a_restart_no_number_is_reused() {
  Rig a;
  for (int i = 0; i < 5; i++) TEST_ASSERT_EQUAL(SubmitResult::Queued, a.send(TxKind::Event));
  // power cycle: new driver and modem, same non-volatile store
  Keys keys = a.keys;
  FakeKim1 modem;
  TxConfig cfg = a.cfg;
  Tx tx(cfg, keys, modem, a.store);
  Record rec;
  TEST_ASSERT_EQUAL(SubmitResult::Queued, tx.submit(TxKind::Event, rec, 0));
  TEST_ASSERT_EQUAL_UINT16(6, rec.seq);
}

void test_a_crash_between_storing_and_sending_only_skips_a_number() {
  Rig a;
  Record rec;
  a.tx->submit(TxKind::Event, rec, a.now);  // seq 1 stored-before-use, never transmitted
  FakeKim1 modem;
  Tx tx(a.cfg, a.keys, modem, a.store);  // reboot
  Record again;
  tx.submit(TxKind::Event, again, 0);
  TEST_ASSERT_TRUE(again.seq > rec.seq);
}

void test_a_failing_store_means_nothing_is_queued_or_sent() {
  Rig r;
  r.store.fail_save = true;
  TEST_ASSERT_EQUAL(SubmitResult::SeqStoreFailed, r.send(TxKind::Event));
  TEST_ASSERT_EQUAL(0, r.tx->pending());
  r.run(5000);
  TEST_ASSERT_EQUAL(0, r.modem.commands);
  r.store.fail_save = false;
  r.store.fail_load = true;
  TEST_ASSERT_EQUAL(SubmitResult::SeqStoreFailed, r.send(TxKind::Event));
}

void test_a_store_that_lies_about_saving_is_caught() {
  Rig r;
  r.store.lie_about_save = true;
  TEST_ASSERT_EQUAL(SubmitResult::SeqStoreFailed, r.send(TxKind::Event));
  TEST_ASSERT_EQUAL(0, r.tx->pending());
}

void test_a_key_id_is_used_up_after_65535_and_then_refuses() {
  Rig r;
  r.store.save(0, 65535);
  Record a, b;
  TEST_ASSERT_EQUAL(SubmitResult::Queued, r.tx->submit(TxKind::Event, a, r.now));
  TEST_ASSERT_EQUAL_UINT16(65535, a.seq);
  TEST_ASSERT_EQUAL(SubmitResult::KeyExhausted, r.tx->submit(TxKind::Event, b, r.now));
}

void test_a_full_queue_does_not_burn_a_sequence_number() {
  TxConfig c;
  c.node_id = kNode;
  Rig r(c);
  for (int i = 0; i < STZ_TX_QUEUE; i++) TEST_ASSERT_EQUAL(SubmitResult::Queued, r.send(TxKind::Event));
  r.modem.reply = FakeKim1::Reply::Silent;
  uint16_t before;
  r.store.load(0, before);
  // all slots hold events still to be sent: a heartbeat cannot displace them
  TEST_ASSERT_EQUAL(SubmitResult::QueueFull, r.send(TxKind::Heartbeat));
  uint16_t after;
  r.store.load(0, after);
  TEST_ASSERT_EQUAL_UINT16(before, after);
}

// ---- transmission policy ----

static int count_substr(const char* s, const char* sub) {
  int n = 0;
  for (; (s = strstr(s, sub)); s += 1) n++;
  return n;
}

void test_a_confirmed_event_goes_out_at_once_and_exactly_the_stored_record() {
  Rig r;
  TEST_ASSERT_EQUAL(SubmitResult::Queued, r.send(TxKind::Event));
  r.run(200);
  TEST_ASSERT_EQUAL(1, r.modem.commands);
  const char* cmd = r.modem.last_command();
  TEST_ASSERT_EQUAL(0, strncmp(cmd, "AT+TX=B1,300,500,", 17));
  // the hex in the command decodes with the node's key to the record that was submitted
  char hex[80];
  const char* h = cmd + 17;
  size_t hl = strcspn(h, "\r\n");
  memcpy(hex, h, hl);
  hex[hl] = 0;
  uint8_t raw[kMaxLen], m[16];
  size_t n = 0;
  TEST_ASSERT_EQUAL(Status::Ok, from_hex(hex, hl, raw, sizeof raw, n));
  master_key(m);
  Record back;
  TEST_ASSERT_EQUAL(Status::Ok, decode(raw, n, kNode, m, back));
  TEST_ASSERT_EQUAL(1, back.seq);
  TEST_ASSERT_EQUAL(kJamming, back.event_class);
}

void test_an_event_repeats_twice_spread_over_time_then_stops() {
  Rig r;
  r.send(TxKind::Event);
  r.run(60000);
  TEST_ASSERT_EQUAL(1, r.modem.commands);  // repeats wait for different satellite passes
  r.run(900000);
  TEST_ASSERT_EQUAL(2, r.modem.commands);
  r.run(900000);
  TEST_ASSERT_EQUAL(3, r.modem.commands);
  r.run(3600000);
  TEST_ASSERT_EQUAL(3, r.modem.commands);  // done; it stays queued, waiting to be acknowledged
  TEST_ASSERT_EQUAL(1, r.tx->pending());
}

void test_a_status_change_is_sent_once() {
  Rig r;
  r.send(TxKind::Status, kNormal);
  r.run(3600000);
  TEST_ASSERT_EQUAL(1, r.modem.commands);
}

void test_an_acknowledgement_stops_the_repeats_and_frees_the_slot() {
  Rig r;
  r.send(TxKind::Event);
  r.run(2000);
  Ack a;
  a.node_id = kNode;
  a.key_id = 0;
  a.max_seq = 1;
  a.signed_at = 100;
  TEST_ASSERT_EQUAL(AckResult::Applied, r.tx->on_ack(a));
  TEST_ASSERT_EQUAL(0, r.tx->pending());
  r.run(3600000);
  TEST_ASSERT_EQUAL(1, r.modem.commands);
}

void test_an_entry_17_behind_the_newest_is_given_up_like_the_cloud_does() {
  Rig r;
  r.send(TxKind::Event);  // seq 1, never acknowledged
  r.run(2000);
  r.run(3 * 900000);  // the burst is done; the event waits for an ack and rides along with heartbeats
  TEST_ASSERT_EQUAL(1, r.tx->pending());
  r.store.save(0, 17);
  r.send(TxKind::Heartbeat);  // seq 17: 1 is 16 behind, still inside the ack window, so it is resent
  r.run(60000);
  TEST_ASSERT_EQUAL(1, r.tx->pending());
  uint32_t sent = r.modem.commands;
  r.send(TxKind::Heartbeat);  // seq 18: 1 is 17 behind, given up at heartbeat time
  r.run(60000);
  TEST_ASSERT_EQUAL(0, r.tx->pending());
  TEST_ASSERT_EQUAL(sent + 1, r.modem.commands);  // only the heartbeat went out
  Ack a;  // and the cloud's ack for {1, 17, 18} says the same: max_seq 18
  a.node_id = kNode;
  a.max_seq = 18;
  a.signed_at = 100;
  TEST_ASSERT_EQUAL(AckResult::Applied, r.tx->on_ack(a));
}

void test_an_event_burst_gives_up_old_entries_without_waiting_for_a_heartbeat() {
  Rig r;
  r.send(TxKind::Event);  // seq 1, never acknowledged
  r.run(2000);
  r.run(3 * 900000);
  TEST_ASSERT_EQUAL(1, r.tx->pending());
  r.store.save(0, 17);
  r.send(TxKind::Event);  // seq 17: 1 is 16 behind, kept
  TEST_ASSERT_EQUAL(2, r.tx->pending());
  r.send(TxKind::Event);  // seq 18: 1 is 17 behind, given up now, not at the next heartbeat
  TEST_ASSERT_EQUAL(2, r.tx->pending());
}

void test_a_selective_acknowledgement_frees_only_the_marked_ones() {
  Rig r;
  r.modem.reply = FakeKim1::Reply::Silent;
  for (int i = 0; i < 4; i++) r.send(TxKind::Status, kNormal);  // seq 1..4
  Ack a;
  a.node_id = kNode;
  a.key_id = 0;
  a.max_seq = 1;
  a.mask = 0x0001;  // bit 0 = seq max_seq + 2 = 3
  a.signed_at = 5;
  r.tx->on_ack(a);
  TEST_ASSERT_EQUAL(2, r.tx->pending());  // seq 2 and 4 remain
}

void test_acknowledgements_for_someone_else_or_stale_are_ignored() {
  Rig r;
  for (int i = 0; i < 3; i++) r.send(TxKind::Status, kNormal);
  Ack a;
  a.node_id = kNode + 1;
  a.max_seq = 3;
  a.signed_at = 10;
  TEST_ASSERT_EQUAL(AckResult::NotForThisNode, r.tx->on_ack(a));
  a.node_id = kNode;
  a.key_id = 5;
  TEST_ASSERT_EQUAL(AckResult::WrongKey, r.tx->on_ack(a));
  TEST_ASSERT_EQUAL(3, r.tx->pending());
  a.key_id = 0;
  a.max_seq = 2;
  a.signed_at = 20;
  TEST_ASSERT_EQUAL(AckResult::Applied, r.tx->on_ack(a));
  Ack old = a;
  old.signed_at = 19;  // an older ack replayed
  old.max_seq = 3;
  TEST_ASSERT_EQUAL(AckResult::Stale, r.tx->on_ack(old));
  Ack back = a;
  back.max_seq = 1;  // going backwards
  back.signed_at = 30;
  TEST_ASSERT_EQUAL(AckResult::Stale, r.tx->on_ack(back));
  TEST_ASSERT_EQUAL(AckResult::Applied, r.tx->on_ack(a));  // an equal repeat is harmless
  TEST_ASSERT_EQUAL(1, r.tx->pending());
}

void test_a_failed_transmission_is_retried_after_a_backoff() {
  Rig r;
  r.modem.reply = FakeKim1::Reply::Fail;
  r.send(TxKind::Event);
  r.run(10000);
  TEST_ASSERT_EQUAL(1, r.modem.commands);
  r.run(60000);
  TEST_ASSERT_EQUAL(2, r.modem.commands);
  r.modem.reply = FakeKim1::Reply::Done;
  r.run(70000);
  TEST_ASSERT_EQUAL(3, r.modem.commands);
}

void test_a_silent_modem_times_out_without_blocking() {
  Rig r;
  r.modem.reply = FakeKim1::Reply::Silent;
  r.send(TxKind::Event);
  r.run(1000);
  TEST_ASSERT_TRUE(r.tx->in_flight());
  r.run(21000);  // past tx_timeout_ms
  TEST_ASSERT_FALSE(r.tx->in_flight());
  r.run(60000);
  TEST_ASSERT_TRUE(r.modem.commands >= 2);  // and it tries again
}

void test_an_entry_gives_up_after_the_failure_limit() {
  Rig r;
  r.modem.reply = FakeKim1::Reply::Fail;
  r.send(TxKind::Event);
  r.run(1200000);
  TEST_ASSERT_EQUAL(r.cfg.max_failures, r.modem.commands);
}

void test_only_one_transmission_is_in_flight_and_they_are_spaced() {
  Rig r;
  r.modem.air_ms = 4000;
  r.send(TxKind::Event);
  r.send(TxKind::Event);
  r.run(3000);
  TEST_ASSERT_EQUAL(1, r.modem.commands);  // the second waits for the first's result
  r.run(20000);
  TEST_ASSERT_EQUAL(2, r.modem.commands);
}

void test_events_go_before_heartbeats() {
  Rig r;
  r.modem.reply = FakeKim1::Reply::Silent;
  r.send(TxKind::Heartbeat, kNormal);
  r.send(TxKind::Event);
  r.run(200);
  TEST_ASSERT_EQUAL(1, r.modem.commands);
  // seq 2 is the event
  TEST_ASSERT_TRUE(strstr(r.modem.last_command(), "AT+TX=") != nullptr);
  uint8_t raw[kMaxLen], m[16];
  size_t n;
  const char* h = r.modem.last_command() + 17;
  from_hex(h, strcspn(h, "\r\n"), raw, sizeof raw, n);
  master_key(m);
  Record back;
  decode(raw, n, kNode, m, back);
  TEST_ASSERT_EQUAL(2, back.seq);
}

void test_a_heartbeat_is_due_hourly_and_carries_unacknowledged_events() {
  Rig r;
  TEST_ASSERT_TRUE(r.tx->heartbeat_due(r.now));  // none yet: send one at boot
  r.send(TxKind::Heartbeat, kNormal);
  TEST_ASSERT_FALSE(r.tx->heartbeat_due(r.now + 1000));
  TEST_ASSERT_TRUE(r.tx->heartbeat_due(r.now + 3600000u));
  r.send(TxKind::Status, kJamming);
  r.run(2000000);
  int before = r.modem.commands;  // heartbeat + status
  TEST_ASSERT_EQUAL(2, before);
  r.send(TxKind::Heartbeat, kNormal);  // next hour's heartbeat: the unacknowledged status rides along
  r.run(60000);
  TEST_ASSERT_EQUAL(before + 2, r.modem.commands);
}

void test_resends_are_limited() {
  Rig r;
  r.send(TxKind::Status, kJamming);
  r.run(20000);
  for (int i = 0; i < 6; i++) {
    r.send(TxKind::Heartbeat, kNormal);
    r.run(120000);
  }
  // 1 original + max_resends carried, plus the six heartbeats themselves
  TEST_ASSERT_EQUAL(1 + r.cfg.max_resends + 6, r.modem.commands);
}

// ---- budget ----

void test_the_daily_budget_holds_and_events_may_use_the_heartbeat_reserve() {
  TxConfig c;
  c.node_id = kNode;
  c.daily_budget = 6;
  c.heartbeat_reserve = 2;
  c.min_gap_ms = 1000;
  Rig r(c);
  for (int i = 0; i < 8; i++) {
    r.send(TxKind::Heartbeat, kNormal);
    r.run(30000);
  }
  TEST_ASSERT_EQUAL(4, r.modem.commands);  // heartbeats stop at budget - reserve
  r.send(TxKind::Event);
  r.run(30000);
  TEST_ASSERT_EQUAL(5, r.modem.commands);  // an event may still go
  r.send(TxKind::Event);
  r.run(30000);
  TEST_ASSERT_EQUAL(6, r.modem.commands);
  r.send(TxKind::Event);
  r.run(30000);
  TEST_ASSERT_EQUAL(6, r.modem.commands);  // the budget itself is a hard limit
  TEST_ASSERT_EQUAL(6, r.tx->sent_last_24h());
}

void test_the_budget_is_a_rolling_day() {
  TxConfig c;
  c.node_id = kNode;
  c.daily_budget = 3;
  c.heartbeat_reserve = 0;
  c.min_gap_ms = 1000;
  Rig r(c);
  for (int i = 0; i < 3; i++) {
    r.send(TxKind::Heartbeat, kNormal);
    r.run(30000);
  }
  TEST_ASSERT_EQUAL(3, r.modem.commands);
  r.send(TxKind::Heartbeat, kNormal);
  r.run(3600000);
  TEST_ASSERT_EQUAL(3, r.modem.commands);
  r.run(24u * 3600000u);
  TEST_ASSERT_EQUAL(4, r.modem.commands);  // sent once the old transmissions aged out
}

// ---- queue pressure ----

void test_a_full_queue_makes_room_for_an_event_by_dropping_the_oldest_heartbeat() {
  Rig r;
  r.modem.reply = FakeKim1::Reply::Silent;
  r.modem.air_ms = 1000000;
  for (int i = 0; i < STZ_TX_QUEUE; i++) TEST_ASSERT_EQUAL(SubmitResult::Queued, r.send(TxKind::Heartbeat, kNormal));
  TEST_ASSERT_EQUAL(SubmitResult::QueueFull, r.send(TxKind::Heartbeat, kNormal));  // a heartbeat never evicts
  TEST_ASSERT_EQUAL(SubmitResult::Queued, r.send(TxKind::Event));
  TEST_ASSERT_EQUAL(STZ_TX_QUEUE, r.tx->pending());
}

void test_retained_unacknowledged_entries_are_evicted_oldest_first() {
  Rig r;
  for (int i = 0; i < STZ_TX_QUEUE; i++) r.send(TxKind::Status, kJamming);
  r.run(20u * 60000u);  // every status goes out once and is then only waiting for an ack
  TEST_ASSERT_EQUAL(STZ_TX_QUEUE, r.tx->pending());
  TEST_ASSERT_EQUAL(SubmitResult::Queued, r.send(TxKind::Event));
  TEST_ASSERT_EQUAL(STZ_TX_QUEUE, r.tx->pending());
}

// ---- blanking ----

void test_blanking_covers_the_transmission_and_a_guard_after_it() {
  Rig r;
  TEST_ASSERT_FALSE(r.tx->blanked(r.now));
  r.send(TxKind::Event);
  r.run(200);  // command written, module on the air
  TEST_ASSERT_TRUE(r.tx->blanked(r.now));
  r.run(1500);  // +TX_INFO: 1 arrives
  TEST_ASSERT_TRUE(r.tx->blanked(r.now));  // guard interval
  r.run(2000);
  TEST_ASSERT_TRUE(r.tx->blanked(r.now));
  r.run(3000);
  TEST_ASSERT_FALSE(r.tx->blanked(r.now));
}

void test_blanking_follows_the_tx_status_pin_even_without_a_command() {
  Rig r;
  r.modem.force_busy_pin = true;
  TEST_ASSERT_TRUE(r.tx->blanked(r.now));
  r.modem.force_busy_pin = false;
  r.now += 100;
  TEST_ASSERT_TRUE(r.tx->blanked(r.now));  // falling edge starts the guard
  r.now += 3500;
  TEST_ASSERT_FALSE(r.tx->blanked(r.now));
}

void test_blanking_follows_a_silent_modem_until_the_timeout() {
  Rig r;
  r.modem.reply = FakeKim1::Reply::Silent;
  r.send(TxKind::Event);
  r.run(15000);
  TEST_ASSERT_TRUE(r.tx->blanked(r.now));
  r.run(60000);
  TEST_ASSERT_FALSE(r.tx->blanked(r.now));
}

void test_our_own_mesh_transmissions_blank_too() {
  Rig r;
  r.tx->note_mesh_tx(r.now, 800);
  TEST_ASSERT_TRUE(r.tx->blanked(r.now + 500));
  TEST_ASSERT_TRUE(r.tx->blanked(r.now + 3000));
  TEST_ASSERT_FALSE(r.tx->blanked(r.now + 5000));
}

void test_blanking_keeps_our_own_kim1_from_looking_like_jamming() {
  // The receiver collapses while the KIM1 transmits. Fed through the blanking flag, the detector stays quiet;
  // fed without it, it would raise a jamming event.
  Rig r;
  DetectConfig dc;
  Detector with(dc), without(dc);
  Epoch e;
  e.fix_ok = true;
  e.sats_tracked = 12;
  e.cn0_mean = 42;
  e.cn0_spread_x10 = 38;
  e.jam_state = 1;
  e.spoof_state = 1;
  for (int i = 0; i < 30; i++) {
    e.mono_ms = r.now;
    with.update(e);
    without.update(e);
    r.now += 1000;
  }
  r.send(TxKind::Event);
  r.run(200);
  int blanked_epochs = 0, jam_with = 0, jam_without = 0;
  for (int i = 0; i < 10; i++) {
    bool on_air = r.tx->blanked(r.now);  // the receiver is only degraded while our transmitter is active
    Epoch seen = e;
    if (on_air) {
      seen.cn0_mean = 20;  // the 401 MHz burst desenses the front end
      seen.jam_state = 2;
      blanked_epochs++;
    }
    seen.mono_ms = r.now;
    Epoch flagged = seen;
    flagged.blanked = on_air;
    if (with.update(flagged).event_class == kJamming) jam_with++;
    if (without.update(seen).event_class == kJamming) jam_without++;
    r.run(1000);
  }
  TEST_ASSERT_TRUE(blanked_epochs >= 4);
  TEST_ASSERT_EQUAL(0, jam_with);
  TEST_ASSERT_TRUE(jam_without > 0);
}

// ---- a node with no KIM1 ----

struct CountingSink : RecordSink {
  int records = 0, acks = 0;
  uint16_t last_seq = 0;
  void on_record(TxKind, uint16_t seq, const uint8_t*, size_t len, uint32_t) override {
    records++;
    last_seq = seq;
    TEST_ASSERT_TRUE(len == kCoreLen || len == kExtLen);
  }
  void on_ack(const Ack&) override { acks++; }
};

void test_without_a_satellite_modem_records_are_numbered_and_handed_to_the_sink_only() {
  TxConfig c;
  c.node_id = kNode;
  c.satellite_enabled = false;
  Rig r(c);
  CountingSink sink;
  r.tx->set_sink(&sink);
  for (int i = 0; i < 40; i++) TEST_ASSERT_EQUAL(SubmitResult::Queued, r.send(i % 3 ? TxKind::Event : TxKind::Heartbeat));
  TEST_ASSERT_EQUAL(40, sink.records);
  TEST_ASSERT_EQUAL_UINT16(40, sink.last_seq);  // sequence numbers still advance and are stored first
  r.run(60000);
  TEST_ASSERT_EQUAL(0, r.modem.commands);  // the modem is never touched
  TEST_ASSERT_EQUAL(0, (int)r.tx->pending());
  TEST_ASSERT_FALSE(r.tx->blanked(r.now));
  r.modem.force_busy_pin = true;  // a floating status pin must not blank a node that has no KIM1
  TEST_ASSERT_FALSE(r.tx->blanked(r.now));
  Ack a;
  a.node_id = kNode;
  a.max_seq = 10;
  a.signed_at = 1;
  TEST_ASSERT_EQUAL(AckResult::Applied, r.tx->on_ack(a));
  TEST_ASSERT_EQUAL(1, sink.acks);
  TEST_ASSERT_EQUAL(1, (int)r.tx->acks_applied());
}

// ---- sanity ----

void test_the_same_inputs_give_the_same_commands() {
  char first[2][160];
  for (int pass = 0; pass < 2; pass++) {
    Rig r;
    r.send(TxKind::Event);
    r.run(200);
    strcpy(first[pass], r.modem.last_command());
  }
  TEST_ASSERT_EQUAL_STRING(first[0], first[1]);
  TEST_ASSERT_EQUAL(1, count_substr(first[0], "AT+TX="));
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_the_at_tx_command_is_built_exactly);
  RUN_TEST(test_commands_with_bad_parameters_are_refused);
  RUN_TEST(test_a_full_size_record_fits_the_command_buffer);
  RUN_TEST(test_tx_info_replies_are_understood);
  RUN_TEST(test_long_garbage_cannot_overflow_or_fake_a_reply);
  RUN_TEST(test_sequence_numbers_start_at_one_and_are_stored_before_use);
  RUN_TEST(test_after_a_restart_no_number_is_reused);
  RUN_TEST(test_a_crash_between_storing_and_sending_only_skips_a_number);
  RUN_TEST(test_a_failing_store_means_nothing_is_queued_or_sent);
  RUN_TEST(test_a_store_that_lies_about_saving_is_caught);
  RUN_TEST(test_a_key_id_is_used_up_after_65535_and_then_refuses);
  RUN_TEST(test_a_full_queue_does_not_burn_a_sequence_number);
  RUN_TEST(test_a_confirmed_event_goes_out_at_once_and_exactly_the_stored_record);
  RUN_TEST(test_an_event_repeats_twice_spread_over_time_then_stops);
  RUN_TEST(test_a_status_change_is_sent_once);
  RUN_TEST(test_an_acknowledgement_stops_the_repeats_and_frees_the_slot);
  RUN_TEST(test_an_entry_17_behind_the_newest_is_given_up_like_the_cloud_does);
  RUN_TEST(test_an_event_burst_gives_up_old_entries_without_waiting_for_a_heartbeat);
  RUN_TEST(test_a_selective_acknowledgement_frees_only_the_marked_ones);
  RUN_TEST(test_acknowledgements_for_someone_else_or_stale_are_ignored);
  RUN_TEST(test_a_failed_transmission_is_retried_after_a_backoff);
  RUN_TEST(test_a_silent_modem_times_out_without_blocking);
  RUN_TEST(test_an_entry_gives_up_after_the_failure_limit);
  RUN_TEST(test_only_one_transmission_is_in_flight_and_they_are_spaced);
  RUN_TEST(test_events_go_before_heartbeats);
  RUN_TEST(test_a_heartbeat_is_due_hourly_and_carries_unacknowledged_events);
  RUN_TEST(test_resends_are_limited);
  RUN_TEST(test_the_daily_budget_holds_and_events_may_use_the_heartbeat_reserve);
  RUN_TEST(test_the_budget_is_a_rolling_day);
  RUN_TEST(test_a_full_queue_makes_room_for_an_event_by_dropping_the_oldest_heartbeat);
  RUN_TEST(test_retained_unacknowledged_entries_are_evicted_oldest_first);
  RUN_TEST(test_blanking_covers_the_transmission_and_a_guard_after_it);
  RUN_TEST(test_blanking_follows_the_tx_status_pin_even_without_a_command);
  RUN_TEST(test_blanking_follows_a_silent_modem_until_the_timeout);
  RUN_TEST(test_our_own_mesh_transmissions_blank_too);
  RUN_TEST(test_blanking_keeps_our_own_kim1_from_looking_like_jamming);
  RUN_TEST(test_without_a_satellite_modem_records_are_numbered_and_handed_to_the_sink_only);
  RUN_TEST(test_the_same_inputs_give_the_same_commands);
  return UNITY_END();
}
