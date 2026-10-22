// The mesh uplink: `STZ1:` text identical to the golden vectors, spacing and caps, signed acknowledgements coming
// back as `STZA1:` lines, and the whole node with both uplinks. Scripted ports, no radio.
#include <string.h>
#include <unity.h>

#include "../test_ack/ack_vectors.h"
#include "../test_codec/vectors.h"
#include "stz_codec.h"
#include "stz_fake_kim1.h"
#include "stz_fake_mesh.h"
#include "stz_node.h"

using namespace stz;

static CloudKey cloud_key() {
  CloudKey k;
  size_t n = 0;
  k.id = kCloudKeyId;
  from_hex(kCloudPublicHex, strlen(kCloudPublicHex), k.pub, 32, n);
  return k;
}

void test_the_text_equals_stz1_plus_the_golden_vector_hex() {
  for (const ValidVector& v : kValid) {
    uint8_t raw[kMaxLen];
    size_t n = 0;
    TEST_ASSERT_EQUAL(Status::Ok, from_hex(v.hex, strlen(v.hex), raw, sizeof raw, n));
    char text[5 + 2 * kMaxLen + 1];
    size_t m = mesh::build_text(raw, n, text, sizeof text);
    TEST_ASSERT_EQUAL_MESSAGE(5 + strlen(v.hex) + 1, m, v.name);
    TEST_ASSERT_EQUAL_MESSAGE('\n', text[m - 1], v.name);
    text[m - 1] = 0;
    char expect[5 + 2 * kMaxLen + 1] = "STZ1:";
    strcat(expect, v.hex);
    TEST_ASSERT_EQUAL_STRING_MESSAGE(expect, text, v.name);
  }
  char tiny[8];
  uint8_t raw[28] = {0};
  TEST_ASSERT_EQUAL(0, mesh::build_text(raw, 28, tiny, sizeof tiny));
}

struct Mesh {
  FakeMesh port;
  CloudKey key = cloud_key();
  mesh::MeshConfig cfg;
  mesh::Link link;
  uint32_t now = 10000;
  Mesh() : link(cfg, port, &key, 1) {}
  mesh::PollResult step(uint32_t ms) {
    mesh::PollResult last;
    for (uint32_t t = 0; t < ms; t += 100) {
      now += 100;
      mesh::PollResult r = link.poll(now);
      if (r.sent) last.sent = true;
      if (r.ack) { last.ack = true; last.ack_data = r.ack_data; }
    }
    return last;
  }
  void record(TxKind kind, uint16_t seq) {
    uint8_t raw[28];
    memset(raw, (uint8_t)seq, sizeof raw);
    link.on_record(kind, seq, raw, sizeof raw, now);
  }
};

void test_an_event_goes_out_at_once_and_once_more_a_little_later() {
  Mesh m;
  m.record(TxKind::Event, 1);
  m.step(200);
  TEST_ASSERT_EQUAL(1, m.port.writes);
  TEST_ASSERT_EQUAL(0, strncmp(m.port.last_line(), "STZ1:", 5));
  m.step(20000);
  TEST_ASSERT_EQUAL(1, m.port.writes);
  m.step(15000);
  TEST_ASSERT_EQUAL(2, m.port.writes);
  m.step(600000);
  TEST_ASSERT_EQUAL(2, m.port.writes);
  TEST_ASSERT_EQUAL(0, (int)m.link.pending());
}

void test_status_and_heartbeat_are_sent_once_and_events_first() {
  Mesh m;
  m.record(TxKind::Heartbeat, 1);
  m.record(TxKind::Status, 2);
  m.record(TxKind::Event, 3);
  m.step(200);
  TEST_ASSERT_EQUAL(1, m.port.writes);
  uint8_t expect = 3;
  char hex2[3] = {"0123456789abcdef"[expect >> 4], "0123456789abcdef"[expect & 15], 0};
  TEST_ASSERT_TRUE(strstr(m.port.last_line(), hex2) != nullptr);  // the event's bytes
  m.step(60000);
  TEST_ASSERT_EQUAL(4, m.port.writes);  // 3 once each + the event's repeat
}

void test_messages_are_spaced_and_capped_per_hour() {
  Mesh m;
  m.cfg.hourly_cap = 3;
  mesh::Link link(m.cfg, m.port, &m.key, 1);
  uint32_t now = 1000;
  uint8_t raw[28] = {0};
  for (int i = 0; i < 10; i++) link.on_record(TxKind::Status, (uint16_t)(i + 1), raw, 28, now);
  int sent = 0;
  for (int t = 0; t < 200; t++) {  // 20 s: spacing allows four sends, the cap allows three
    now += 100;
    if (link.poll(now).sent) sent++;
  }
  TEST_ASSERT_EQUAL(3, sent);
  for (int t = 0; t < 40000; t++) {  // the window passes
    now += 100;
    if (link.poll(now).sent) sent++;
  }
  TEST_ASSERT_TRUE(sent > 3);
}

void test_a_failing_port_never_blocks_and_retries() {
  Mesh m;
  m.port.fail_writes = true;
  m.record(TxKind::Event, 1);
  m.step(5000);
  TEST_ASSERT_EQUAL(1, (int)m.link.pending());
  m.port.fail_writes = false;
  m.step(3000);
  TEST_ASSERT_EQUAL(1, m.port.writes);
}

void test_a_full_queue_drops_heartbeats_before_events() {
  Mesh m;
  m.port.fail_writes = true;
  for (int i = 0; i < STZ_MESH_QUEUE; i++) m.record(TxKind::Heartbeat, (uint16_t)(i + 1));
  m.record(TxKind::Event, (uint16_t)(STZ_MESH_QUEUE + 1));
  TEST_ASSERT_EQUAL(STZ_MESH_QUEUE, (int)m.link.pending());
  m.port.fail_writes = false;
  m.step(300);
  TEST_ASSERT_TRUE(strstr(m.port.last_line(), "STZ1:") != nullptr);
  // a heartbeat does not displace a queued event
  Mesh n;
  n.port.fail_writes = true;
  for (int i = 0; i < STZ_MESH_QUEUE; i++) n.record(TxKind::Event, (uint16_t)(i + 1));
  n.record(TxKind::Heartbeat, (uint16_t)(STZ_MESH_QUEUE + 1));
  TEST_ASSERT_EQUAL(STZ_MESH_QUEUE, (int)n.link.pending());
}

// ---- acknowledgements coming back ----

static void say_line(FakeMesh& p, const char* text) {
  p.say(text);
  p.say("\n");
}

void test_valid_signed_acks_are_accepted_from_the_serial_module() {
  for (const ValidAck& v : kValidAcks) {
    Mesh m;
    say_line(m.port, v.text);
    mesh::PollResult r = m.step(200);
    TEST_ASSERT_TRUE_MESSAGE(r.ack, v.name);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(v.node, r.ack_data.node_id, v.name);
    TEST_ASSERT_EQUAL_UINT16_MESSAGE(v.max_seq, r.ack_data.max_seq, v.name);
  }
}

void test_forged_and_malformed_ack_lines_are_ignored() {
  for (const InvalidAck& v : kInvalidAcks) {
    Mesh m;
    if (v.text[0]) say_line(m.port, v.text);
    else {
      char text[6 + 2 * 100 + 1] = "STZA1:";
      strncat(text, v.hex, 200);
      say_line(m.port, text);
    }
    TEST_ASSERT_FALSE_MESSAGE(m.step(300).ack, v.name);
  }
}

void test_chat_garbage_and_partial_lines_do_not_confuse_it() {
  Mesh m;
  m.port.say("hello from the channel\nSTZ1:00112233\n");
  char junk[400];
  memset(junk, 'z', sizeof junk - 1);
  junk[sizeof junk - 1] = 0;
  say_line(m.port, junk);
  const ValidAck& v = kValidAcks[0];
  size_t half = strlen(v.text) / 2;
  char first[200];
  memcpy(first, v.text, half);
  first[half] = 0;
  m.port.say(first);  // the line arrives in two pieces
  TEST_ASSERT_FALSE(m.step(300).ack);
  m.port.say(v.text + half);
  m.port.say("\r\n");
  TEST_ASSERT_TRUE(m.step(300).ack);
}

void test_an_ack_drops_the_pending_mesh_sends() {
  Mesh m;
  m.port.fail_writes = true;
  m.record(TxKind::Event, 1);
  m.record(TxKind::Event, 2);
  m.record(TxKind::Event, 7);
  Ack a;
  a.max_seq = 2;
  m.link.on_ack(a);
  TEST_ASSERT_EQUAL(1, (int)m.link.pending());
}

void test_a_slot_17_behind_the_newest_record_is_given_up() {
  Mesh m;
  m.port.fail_writes = true;
  m.record(TxKind::Event, 1);
  m.record(TxKind::Event, 17);  // 1 is 16 behind: kept
  TEST_ASSERT_EQUAL(2, (int)m.link.pending());
  m.record(TxKind::Event, 18);  // 1 is 17 behind: given up, as the cloud does
  TEST_ASSERT_EQUAL(2, (int)m.link.pending());
}

// ---- the whole node ----

struct Full {
  FakeKim1 kim;
  FakeMesh mport;
  FakeStore store;
  Keys keys;
  TxConfig tc;
  CloudKey ck = cloud_key();
  mesh::Link* link;
  Tx* tx;
  Node* node;
  uint32_t now = 1000;
  Epoch base;
  Full() {
    uint8_t m[16];
    for (int i = 0; i < 16; i++) m[i] = (uint8_t)i;
    tc.node_id = 1;
    derive_keys(m, 1, 0, keys);
    tx = new Tx(tc, keys, kim, store);
    link = new mesh::Link(mesh::MeshConfig{}, mport, &ck, 1);
    DetectConfig dc;
    dc.fixed_node = true;
    dc.surveyed_lat_e7 = 425000000;
    dc.surveyed_lon_e7 = 270000000;
    node = new Node(dc, *tx, link);
    base.fix_ok = true;
    base.lat_e7 = 425000000;
    base.lon_e7 = 270000000;
    base.sats_tracked = 12;
    base.cn0_mean = 42;
    base.cn0_spread_x10 = 38;
    base.jam_state = 1;
    base.spoof_state = 1;
    base.sensor_stationary = true;
  }
  ~Full() { delete node; delete link; delete tx; }
  Verdict second(Epoch e) {
    e.utc_s = 1795775405u + (now - 1000) / 1000;
    e.mono_ms = now;
    Verdict v = node->on_epoch(e, now);
    for (int i = 0; i < 10; i++) {
      now += 100;
      kim.step(now);
      node->tick(now);
    }
    return v;
  }
};

void test_one_event_reaches_both_the_satellite_and_the_mesh_with_the_same_bytes() {
  Full f;
  for (int i = 0; i < 40; i++) f.second(f.base);
  Epoch hit = f.base;
  hit.cn0_mean = 25;
  hit.jam_ind = 40;  // the receiver's noise indicator is up: jamming, not loss of reception
  for (int i = 0; i < 10; i++) f.second(hit);
  TEST_ASSERT_EQUAL(kJamming, f.node->current_class());
  TEST_ASSERT_TRUE(f.kim.commands >= 2);   // boot heartbeat + the event
  TEST_ASSERT_TRUE(f.mport.writes >= 2);
  // find the event on the mesh and on the KIM1 and compare the hex
  const char* mesh_line = f.mport.last_line();
  TEST_ASSERT_EQUAL(0, strncmp(mesh_line, "STZ1:", 5));
  char want[80];
  const char* sat = f.kim.last_command() + 17;  // after "AT+TX=B1,300,500,"
  size_t hl = strcspn(sat, "\r\n");
  memcpy(want, sat, hl);
  want[hl] = 0;
  for (char* c = want; *c; c++) if (*c >= 'A' && *c <= 'F') *c = (char)(*c + 32);
  TEST_ASSERT_EQUAL_STRING(want, mesh_line + 5);
}

void test_the_mesh_flag_is_set_on_records_when_a_mesh_radio_is_present() {
  Full f;
  for (int i = 0; i < 5; i++) f.second(f.base);
  const char* line = f.mport.last_line();
  uint8_t raw[kMaxLen], m[16];
  size_t n = 0;
  from_hex(line + 5, strlen(line + 5), raw, sizeof raw, n);
  for (int i = 0; i < 16; i++) m[i] = (uint8_t)i;
  Record back;
  TEST_ASSERT_EQUAL(Status::Ok, decode(raw, n, 1, m, back));
  TEST_ASSERT_TRUE(back.mesh);
}

void test_an_ack_over_the_mesh_stops_the_satellite_repeats_too() {
  Full f;
  for (int i = 0; i < 40; i++) f.second(f.base);
  Epoch hit = f.base;
  hit.cn0_mean = 25;
  hit.jam_ind = 40;  // the receiver's noise indicator is up: jamming, not loss of reception
  for (int i = 0; i < 6; i++) f.second(hit);
  size_t before = f.tx->pending();
  TEST_ASSERT_TRUE(before >= 1);
  f.mport.say(kValidAcks[0].text);  // ack_basic: node 1, key 0, max_seq 5
  f.mport.say("\n");
  for (int i = 0; i < 5; i++) f.second(hit);
  TEST_ASSERT_EQUAL(0, (int)f.tx->pending());
}

void test_our_own_mesh_burst_blanks_the_detector() {
  Full f;
  for (int i = 0; i < 40; i++) f.second(f.base);
  TEST_ASSERT_FALSE(f.tx->blanked(f.now));
  f.tx->note_mesh_tx(f.now, 2000);
  TEST_ASSERT_TRUE(f.tx->blanked(f.now + 1000));
  TEST_ASSERT_FALSE(f.tx->blanked(f.now + 9000));
}

void test_without_a_mesh_radio_nothing_changes() {
  FakeKim1 kim;
  FakeStore store;
  Keys keys;
  uint8_t m[16];
  for (int i = 0; i < 16; i++) m[i] = (uint8_t)i;
  derive_keys(m, 1, 0, keys);
  TxConfig tc;
  tc.node_id = 1;
  Tx tx(tc, keys, kim, store);
  Node node(DetectConfig{}, tx);  // no mesh link
  Epoch e;
  e.fix_ok = true;
  e.sats_tracked = 12;
  e.cn0_mean = 42;
  uint32_t now = 1000;
  for (int i = 0; i < 20; i++) {
    e.mono_ms = now;
    e.utc_s = 1795775405u + i;
    node.on_epoch(e, now);
    now += 1000;
    kim.step(now);
    node.tick(now);
  }
  TEST_ASSERT_TRUE(kim.commands >= 1);
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_the_text_equals_stz1_plus_the_golden_vector_hex);
  RUN_TEST(test_an_event_goes_out_at_once_and_once_more_a_little_later);
  RUN_TEST(test_status_and_heartbeat_are_sent_once_and_events_first);
  RUN_TEST(test_messages_are_spaced_and_capped_per_hour);
  RUN_TEST(test_a_failing_port_never_blocks_and_retries);
  RUN_TEST(test_a_full_queue_drops_heartbeats_before_events);
  RUN_TEST(test_valid_signed_acks_are_accepted_from_the_serial_module);
  RUN_TEST(test_forged_and_malformed_ack_lines_are_ignored);
  RUN_TEST(test_chat_garbage_and_partial_lines_do_not_confuse_it);
  RUN_TEST(test_an_ack_drops_the_pending_mesh_sends);
  RUN_TEST(test_a_slot_17_behind_the_newest_record_is_given_up);
  RUN_TEST(test_one_event_reaches_both_the_satellite_and_the_mesh_with_the_same_bytes);
  RUN_TEST(test_the_mesh_flag_is_set_on_records_when_a_mesh_radio_is_present);
  RUN_TEST(test_an_ack_over_the_mesh_stops_the_satellite_repeats_too);
  RUN_TEST(test_our_own_mesh_burst_blanks_the_detector);
  RUN_TEST(test_without_a_mesh_radio_nothing_changes);
  return UNITY_END();
}
