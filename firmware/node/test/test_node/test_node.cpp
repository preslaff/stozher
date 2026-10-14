// The decision loop end to end on the host: UBX-free epochs -> detector -> queue -> AT+TX on a scripted KIM1.
// Epochs are SYNTHETIC. The uplink runs against the fake modem, not a real one.
#include <string.h>
#include <unity.h>

#include "stz_fake_kim1.h"
#include "stz_node.h"

using namespace stz;

struct Bench {
  FakeKim1 modem;
  FakeStore store;
  Keys keys;
  TxConfig tc;
  Tx* tx;
  Node* node;
  uint32_t now = 1000;
  Epoch base;
  int seen_commands = 0;

  Bench() {
    uint8_t m[16];
    for (int i = 0; i < 16; i++) m[i] = (uint8_t)i;
    tc.node_id = 1;
    derive_keys(m, 1, 0, keys);
    tx = new Tx(tc, keys, modem, store);
    DetectConfig dc;
    dc.fixed_node = true;
    dc.surveyed_lat_e7 = 425000000;
    dc.surveyed_lon_e7 = 270000000;
    node = new Node(dc, *tx);
    base.fix_ok = true;
    base.lat_e7 = 425000000;
    base.lon_e7 = 270000000;
    base.utc_s = 1795775405u;
    base.sats_tracked = 12;
    base.cn0_mean = 42;
    base.cn0_spread_x10 = 38;
    base.jam_state = 1;
    base.spoof_state = 1;
    base.sensor_stationary = true;
  }
  ~Bench() {
    delete node;
    delete tx;
  }
  // One second: the epoch arrives, the loop ticks in 100 ms steps.
  Verdict second(const Epoch& e_in, bool receiver_hit_by_own_tx = false) {
    Epoch e = e_in;
    e.utc_s = base.utc_s + (now - 1000) / 1000;
    e.mono_ms = now;  // the epoch builder stamps this in the real loop
    if (receiver_hit_by_own_tx && tx->blanked(now)) {  // physics: our burst really does upset the receiver
      e.cn0_mean = 20;
      e.jam_state = 2;
    }
    Verdict v = node->on_epoch(e, now);
    for (int i = 0; i < 10; i++) {
      now += 100;
      modem.step(now);
      node->tick(now);
    }
    return v;
  }
};

void test_a_healthy_node_sends_one_heartbeat_and_no_events() {
  Bench b;
  for (int i = 0; i < 120; i++) b.second(b.base);
  TEST_ASSERT_EQUAL(1, b.modem.commands);  // the boot heartbeat
  TEST_ASSERT_EQUAL(kNormal, b.node->current_class());
}

void test_jamming_becomes_an_event_on_the_air_within_seconds() {
  Bench b;
  for (int i = 0; i < 40; i++) b.second(b.base);
  int before = b.modem.commands;
  Epoch hit = b.base;
  hit.cn0_mean = 25;
  hit.jam_ind = 40;  // the receiver's noise indicator is up: jamming, not loss of reception
  hit.sats_tracked = 5;
  int latency = -1;
  for (int i = 0; i < 20 && latency < 0; i++) {
    b.second(hit);
    if (b.modem.commands > before) latency = i;
  }
  TEST_ASSERT_TRUE(latency >= 0);
  TEST_ASSERT_TRUE(latency <= 6);  // 3 epochs to confirm, then the next free slot
  TEST_ASSERT_EQUAL(kJamming, b.node->current_class());
  TEST_ASSERT_EQUAL(SubmitResult::Queued, b.node->last_submit());
}

void test_the_end_of_the_event_is_a_status_change_sent_once() {
  Bench b;
  for (int i = 0; i < 40; i++) b.second(b.base);
  Epoch hit = b.base;
  hit.cn0_mean = 25;
  hit.jam_ind = 40;  // the receiver's noise indicator is up: jamming, not loss of reception
  for (int i = 0; i < 20; i++) b.second(hit);
  for (int i = 0; i < 40; i++) b.second(b.base);
  TEST_ASSERT_EQUAL(kNormal, b.node->current_class());
  int cmds = b.modem.commands;
  for (int i = 0; i < 1200; i++) b.second(b.base);  // twenty quiet minutes: only repeats of the event remain
  TEST_ASSERT_TRUE(b.modem.commands >= cmds);
  TEST_ASSERT_TRUE(b.modem.commands <= cmds + 2);
}

void test_our_own_burst_does_not_raise_an_alarm() {
  Bench b;
  for (int i = 0; i < 40; i++) b.second(b.base);
  // a heartbeat or other transmission is on the air while the receiver is degraded by it
  Epoch e = b.base;
  Record r;
  r.type = kEvent;
  r.event_class = kSpoofing;
  b.tx->submit(TxKind::Event, r, b.now);  // sent by the next ticks
  int alarms = 0;
  for (int i = 0; i < 6; i++) {
    Verdict v = b.second(e, /*receiver_hit_by_own_tx=*/true);
    if (v.changed) alarms++;
  }
  TEST_ASSERT_EQUAL(0, alarms);
  TEST_ASSERT_EQUAL(kNormal, b.node->current_class());
}

void test_the_heartbeat_waits_for_a_position() {
  Bench b;
  for (int i = 0; i < 20; i++) {
    b.now += 1000;
    b.node->tick(b.now);
  }
  TEST_ASSERT_EQUAL(0, b.modem.commands);  // no epoch yet: nothing worth reporting
  b.second(b.base);
  b.second(b.base);
  TEST_ASSERT_EQUAL(1, b.modem.commands);
}

void test_a_dead_store_stops_events_but_never_the_detection() {
  Bench b;
  for (int i = 0; i < 40; i++) b.second(b.base);
  b.store.fail_save = true;
  Epoch hit = b.base;
  hit.cn0_mean = 25;
  hit.jam_ind = 40;  // the receiver's noise indicator is up: jamming, not loss of reception
  for (int i = 0; i < 10; i++) b.second(hit);
  TEST_ASSERT_EQUAL(kJamming, b.node->current_class());  // the node still knows
  TEST_ASSERT_EQUAL(SubmitResult::SeqStoreFailed, b.node->last_submit());  // but will not send what it cannot number
}

void test_test_mode_marks_every_record_as_a_test_record() {
  Bench b;
  b.node->set_test_mode(true);
  for (int i = 0; i < 40; i++) b.second(b.base);
  Epoch hit = b.base;
  hit.cn0_mean = 25;
  hit.jam_ind = 40;  // the receiver's noise indicator is up: jamming, not loss of reception
  for (int i = 0; i < 10; i++) b.second(hit);
  TEST_ASSERT_EQUAL(kJamming, b.node->current_class());
  // decode what went to the modem: the event and the heartbeat are both type 3
  const char* h = b.modem.last_command() + 17;
  uint8_t raw[kMaxLen], m[16];
  size_t n = 0;
  from_hex(h, strcspn(h, "\r\n"), raw, sizeof raw, n);
  for (int i = 0; i < 16; i++) m[i] = (uint8_t)i;
  Record back;
  TEST_ASSERT_EQUAL(Status::Ok, decode(raw, n, 1, m, back));
  TEST_ASSERT_EQUAL(kTest, back.type);
  TEST_ASSERT_EQUAL(kJamming, back.event_class);  // the content is still what the detector saw
}

// A heartbeat sent while an event is active repeats the event's class with its confidence and drop (it used to
// say jamming with confidence 0 and drop 0).
void test_a_heartbeat_during_an_event_carries_the_events_confidence() {
  Bench b;
  for (int i = 0; i < 40; i++) b.second(b.base);
  Epoch hit = b.base;
  hit.cn0_mean = 25;
  hit.jam_ind = 40;
  for (int i = 0; i < 3700; i++) b.second(hit);
  TEST_ASSERT_EQUAL(kJamming, b.node->current_class());
  const char* h = b.modem.last_command() + 17;
  uint8_t raw[kMaxLen], m[16];
  size_t n = 0;
  from_hex(h, strcspn(h, "\r\n"), raw, sizeof raw, n);
  for (int i = 0; i < 16; i++) m[i] = (uint8_t)i;
  Record back;
  TEST_ASSERT_EQUAL(Status::Ok, decode(raw, n, 1, m, back));
  TEST_ASSERT_EQUAL(kHeartbeat, back.type);
  TEST_ASSERT_EQUAL(kJamming, back.event_class);
  TEST_ASSERT_TRUE(back.confidence > 0);
  TEST_ASSERT_TRUE(back.cn0_drop >= 10);
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_a_healthy_node_sends_one_heartbeat_and_no_events);
  RUN_TEST(test_a_heartbeat_during_an_event_carries_the_events_confidence);
  RUN_TEST(test_jamming_becomes_an_event_on_the_air_within_seconds);
  RUN_TEST(test_the_end_of_the_event_is_a_status_change_sent_once);
  RUN_TEST(test_our_own_burst_does_not_raise_an_alarm);
  RUN_TEST(test_the_heartbeat_waits_for_a_position);
  RUN_TEST(test_a_dead_store_stops_events_but_never_the_detection);
  RUN_TEST(test_test_mode_marks_every_record_as_a_test_record);
  return UNITY_END();
}
