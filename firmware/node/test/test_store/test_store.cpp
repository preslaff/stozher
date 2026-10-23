// Store and forward on a gateway: records kept until a signed ack shows the backend has them, per-sender quota,
// heartbeats first out, survives a reboot, damaged slots dropped. A RAM backend stands in for the gateway's flash.
#include <string.h>

#include <initializer_list>
#include <unity.h>

#include "../test_ack/ack_vectors.h"
#include "../test_codec/vectors.h"
#include "stz_mesh.h"
#include "stz_store.h"

using namespace stz;
using store::AddResult;
using store::Entry;
using store::Store;

struct Ram : store::Backend {
  uint8_t slots[STZ_STORE_SLOTS][store::kSlotBytes] = {};
  bool fail_writes = false;
  bool read(size_t i, uint8_t out[store::kSlotBytes]) override {
    memcpy(out, slots[i], store::kSlotBytes);
    return true;
  }
  bool write(size_t i, const uint8_t in[store::kSlotBytes]) override {
    if (fail_writes) return false;
    memcpy(slots[i], in, store::kSlotBytes);
    return true;
  }
};

static Ram ram;  // large: keep them off the stack
static Store st(ram);

static const uint32_t A = 0x0b533538, B = 0x604e1da4;

// A 28-byte record with a clear header: type (0 heartbeat, 1 event), sequence number and key id. The body is filler.
static void rec(uint8_t out[28], uint8_t type, uint16_t seq, uint8_t key = 0, uint8_t fill = 0x55) {
  memset(out, fill, 28);
  out[0] = (uint8_t)(0x10 | type);
  out[1] = (uint8_t)(seq >> 8);
  out[2] = (uint8_t)seq;
  out[3] = (uint8_t)(key << 4);
}
static AddResult add(uint32_t from, uint8_t type, uint16_t seq, uint8_t key = 0, uint32_t t = 1795860000u) {
  uint8_t r[28];
  rec(r, type, seq, key);
  return st.add(from, t, r, 28);
}
static bool holds(uint32_t from, uint16_t seq, uint8_t key = 0) {
  Entry e;
  for (uint32_t after = 0; st.next(after, e); after = e.order)
    if (e.from == from && (uint16_t)(e.raw[1] << 8 | e.raw[2]) == seq && (e.raw[3] >> 4) == key) return true;
  return false;
}
static Ack ack(uint8_t key, uint16_t max_seq, uint16_t mask) {
  Ack a;
  a.cloud_key_id = 1;
  a.key_id = key;
  a.max_seq = max_seq;
  a.mask = mask;
  return a;
}

void setUp() {
  memset(ram.slots, 0, sizeof ram.slots);
  ram.fail_writes = false;
  st.begin();
}
void tearDown() {}

void test_the_mesh_text_of_every_golden_record_parses_back_to_its_bytes() {
  for (const ValidVector& v : kValid) {
    uint8_t raw[kMaxLen], back[kMaxLen];
    size_t n = 0, m = 0;
    from_hex(v.hex, strlen(v.hex), raw, sizeof raw, n);
    char text[5 + 2 * kMaxLen + 2];
    size_t t = mesh::build_text(raw, n, text, sizeof text);
    TEST_ASSERT_TRUE_MESSAGE(store::parse_text(text, t, back, m), v.name);
    TEST_ASSERT_EQUAL(n, m);
    TEST_ASSERT_EQUAL_MEMORY(raw, back, n);
  }
  uint8_t raw[kMaxLen];
  size_t n = 0;
  TEST_ASSERT_FALSE(store::parse_text("hello", 5, raw, n));
  TEST_ASSERT_FALSE(store::parse_text("STZA1:00", 8, raw, n));
  TEST_ASSERT_FALSE(store::parse_text("STZ1:1001", 9, raw, n));  // too short
  char wrong_version[5 + 56 + 1] = "STZ1:";
  memset(wrong_version + 5, '2', 56);
  TEST_ASSERT_FALSE(store::parse_text(wrong_version, 61, raw, n));
}

void test_a_record_is_stored_once_and_replayed_in_arrival_order() {
  TEST_ASSERT_EQUAL(AddResult::Stored, add(A, 0, 5));
  TEST_ASSERT_EQUAL(AddResult::Stored, add(B, 1, 9));
  TEST_ASSERT_EQUAL(AddResult::Duplicate, add(A, 0, 5));  // a mesh repeat
  TEST_ASSERT_EQUAL(AddResult::Stored, add(A, 1, 6));
  TEST_ASSERT_EQUAL(AddResult::Stored, add(A, 1, 6, 2));  // same seq, other key id: another record
  TEST_ASSERT_EQUAL(4, st.counts().held);
  TEST_ASSERT_EQUAL(1, st.counts().duplicates);
  Entry e;
  uint16_t seqs[4];
  uint32_t froms[4];
  int n = 0;
  for (uint32_t after = 0; st.next(after, e); after = e.order) {
    seqs[n] = (uint16_t)(e.raw[1] << 8 | e.raw[2]);
    froms[n++] = e.from;
    TEST_ASSERT_EQUAL(1795860000u, e.rx_time);
    TEST_ASSERT_EQUAL(28, e.len);
  }
  TEST_ASSERT_EQUAL(4, n);
  TEST_ASSERT_EQUAL(5, seqs[0]);
  TEST_ASSERT_EQUAL(9, seqs[1]);
  TEST_ASSERT_EQUAL(B, froms[1]);
  TEST_ASSERT_EQUAL(6, seqs[2]);
  TEST_ASSERT_EQUAL(6, seqs[3]);
}

void test_invalid_input_is_refused() {
  uint8_t r[28];
  rec(r, 1, 1);
  TEST_ASSERT_EQUAL(AddResult::Invalid, st.add(0, 0, r, 28));   // no sender
  TEST_ASSERT_EQUAL(AddResult::Invalid, st.add(A, 0, r, 27));
  TEST_ASSERT_EQUAL(AddResult::Invalid, st.add(A, 0, r, 32));   // 32 bytes without the extended flag
  r[0] = 0x21;
  TEST_ASSERT_EQUAL(AddResult::Invalid, st.add(A, 0, r, 28));   // version 2
  TEST_ASSERT_EQUAL(0, st.counts().held);
}

void test_an_ack_deletes_exactly_what_the_backend_has() {
  for (uint16_t s = 1; s <= 8; s++) add(A, 1, s);
  add(A, 1, 3, 2);  // other key id
  add(B, 1, 3);     // other sender
  // max_seq 4, mask bits 0 and 2: seqs 6 and 8 received, 5 and 7 missing
  TEST_ASSERT_EQUAL(6, st.apply_ack(A, ack(0, 4, 0b101)));
  TEST_ASSERT_FALSE(holds(A, 1));
  TEST_ASSERT_FALSE(holds(A, 4));
  TEST_ASSERT_TRUE(holds(A, 5));
  TEST_ASSERT_FALSE(holds(A, 6));
  TEST_ASSERT_TRUE(holds(A, 7));
  TEST_ASSERT_FALSE(holds(A, 8));
  TEST_ASSERT_TRUE(holds(A, 3, 2));
  TEST_ASSERT_TRUE(holds(B, 3));
  TEST_ASSERT_EQUAL(6, st.counts().deleted_by_ack);
  TEST_ASSERT_EQUAL(0, st.apply_ack(0x12345678, ack(0, 100, 0)));  // a node the store has nothing from
}

void test_records_the_cloud_gave_up_are_kept_to_fill_the_gap() {
  // The cloud holds up to 40 and gave up 5..23 (17 or more behind): max_seq 23 means "received or given up".
  for (uint16_t s : {5, 20, 23, 24, 30, 40}) add(A, 1, s);
  // max_seq 30 = 23 given up + 24..30 received; mask: 40 (bit 8)
  TEST_ASSERT_EQUAL(3, st.apply_ack(A, ack(0, 30, 1u << 8)));
  TEST_ASSERT_TRUE(holds(A, 5));
  TEST_ASSERT_TRUE(holds(A, 20));
  TEST_ASSERT_TRUE(holds(A, 23));  // exactly 17 behind 40: given up, so unknown, kept
  TEST_ASSERT_FALSE(holds(A, 24));
  TEST_ASSERT_FALSE(holds(A, 30));
  TEST_ASSERT_FALSE(holds(A, 40));
}

void test_only_a_verified_ack_text_changes_anything() {
  CloudKey k;
  size_t n = 0;
  k.id = kCloudKeyId;
  from_hex(kCloudPublicHex, strlen(kCloudPublicHex), k.pub, 32, n);
  // ack_with_selective_mask: key id 2, max_seq 10, mask 0x8005 -> newest 27, given up to 10: seqs 12, 14 and 27 known
  const ValidAck& v = kValidAcks[1];
  for (uint16_t s : {9, 10, 12, 13, 14, 27}) add(A, 1, s, 2);
  for (const InvalidAck& bad : kInvalidAcks) {
    if (!bad.text[0]) continue;
    TEST_ASSERT_EQUAL_MESSAGE(-1, st.apply_ack_text(A, bad.text, strlen(bad.text), &k, 1), bad.name);
  }
  char tampered[200];
  strcpy(tampered, v.text);
  tampered[strlen(tampered) - 1] = tampered[strlen(tampered) - 1] == '0' ? '1' : '0';
  TEST_ASSERT_EQUAL(-1, st.apply_ack_text(A, tampered, strlen(tampered), &k, 1));
  TEST_ASSERT_EQUAL(-1, st.apply_ack_text(A, v.text, strlen(v.text), nullptr, 0));  // no trusted key: nothing
  TEST_ASSERT_EQUAL(6, st.counts().held);
  TEST_ASSERT_EQUAL(3, st.apply_ack_text(A, v.text, strlen(v.text), &k, 1));
  TEST_ASSERT_TRUE(holds(A, 9, 2));
  TEST_ASSERT_TRUE(holds(A, 10, 2));
  TEST_ASSERT_TRUE(holds(A, 13, 2));
  TEST_ASSERT_FALSE(holds(A, 12, 2));
  TEST_ASSERT_FALSE(holds(A, 27, 2));
}

void test_a_sender_at_its_quota_loses_its_oldest_heartbeat_first() {
  add(A, 1, 1);                // an event first
  add(A, 0, 2);                // then a heartbeat
  for (uint16_t s = 3; s <= STZ_STORE_QUOTA; s++) add(A, 1, s);
  add(B, 0, 1);
  TEST_ASSERT_EQUAL(STZ_STORE_QUOTA, st.held_from(A));
  TEST_ASSERT_EQUAL(AddResult::Stored, add(A, 1, STZ_STORE_QUOTA + 1));
  TEST_ASSERT_EQUAL(STZ_STORE_QUOTA, st.held_from(A));
  TEST_ASSERT_FALSE(holds(A, 2));  // the heartbeat went, though the event is older
  TEST_ASSERT_TRUE(holds(A, 1));
  TEST_ASSERT_EQUAL(AddResult::Stored, add(A, 1, STZ_STORE_QUOTA + 2));
  TEST_ASSERT_FALSE(holds(A, 1));  // no heartbeat left: the oldest record
  TEST_ASSERT_TRUE(holds(B, 1));   // another sender's records are never touched by A's quota
  TEST_ASSERT_EQUAL(2, st.counts().evicted);
}

void test_a_full_store_makes_the_sender_with_the_most_give_way() {
  // Fill the store with senders 1..4 at their quota (the store holds exactly 4 quotas), then a new sender arrives.
  for (uint32_t who = 1; who <= 4; who++)
    for (uint16_t s = 1; s <= STZ_STORE_QUOTA; s++) add(who, 1, s);
  TEST_ASSERT_EQUAL(STZ_STORE_SLOTS, st.counts().held);
  TEST_ASSERT_EQUAL(AddResult::Stored, add(A, 1, 1));
  TEST_ASSERT_EQUAL(STZ_STORE_SLOTS, st.counts().held);
  TEST_ASSERT_EQUAL(1, st.held_from(A));
  TEST_ASSERT_EQUAL(STZ_STORE_QUOTA - 1, st.held_from(1));  // all equal: the first in the table, its oldest record
  TEST_ASSERT_FALSE(holds(1, 1));
}

void test_the_store_survives_a_reboot_and_drops_damaged_slots() {
  add(A, 1, 1);
  add(A, 0, 2);
  add(B, 1, 7);
  ram.slots[1][20] ^= 0xFF;  // a write cut by a power loss in the middle of slot 1 (A's seq 2)
  st.begin();                // reboot
  TEST_ASSERT_EQUAL(2, st.counts().held);
  TEST_ASSERT_TRUE(holds(A, 1));
  TEST_ASSERT_FALSE(holds(A, 2));
  TEST_ASSERT_TRUE(holds(B, 7));
  TEST_ASSERT_EQUAL(AddResult::Duplicate, add(B, 1, 7));
  TEST_ASSERT_EQUAL(AddResult::Stored, add(B, 1, 8));
  Entry e;
  uint32_t last = 0;
  for (uint32_t after = 0; st.next(after, e); after = e.order) last = e.order;
  TEST_ASSERT_EQUAL(4, last);  // arrival order continues after the reboot, never reused
}

void test_a_failed_flash_write_stores_nothing() {
  ram.fail_writes = true;
  TEST_ASSERT_EQUAL(AddResult::NoRoom, add(A, 1, 1));
  TEST_ASSERT_EQUAL(0, st.counts().held);
}

void test_old_records_expire_but_records_without_a_time_stay() {
  add(A, 1, 1, 0, 1000);
  add(A, 1, 2, 0, 0);  // the gateway had no valid time
  add(A, 1, 3, 0, 9000);
  TEST_ASSERT_EQUAL(0, st.expire(0, 100));  // no valid time now: nothing
  TEST_ASSERT_EQUAL(1, st.expire(9050, 7000));
  TEST_ASSERT_FALSE(holds(A, 1));
  TEST_ASSERT_TRUE(holds(A, 2));
  TEST_ASSERT_TRUE(holds(A, 3));
  TEST_ASSERT_EQUAL(1, st.counts().expired);
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_the_mesh_text_of_every_golden_record_parses_back_to_its_bytes);
  RUN_TEST(test_a_record_is_stored_once_and_replayed_in_arrival_order);
  RUN_TEST(test_invalid_input_is_refused);
  RUN_TEST(test_an_ack_deletes_exactly_what_the_backend_has);
  RUN_TEST(test_records_the_cloud_gave_up_are_kept_to_fill_the_gap);
  RUN_TEST(test_only_a_verified_ack_text_changes_anything);
  RUN_TEST(test_a_sender_at_its_quota_loses_its_oldest_heartbeat_first);
  RUN_TEST(test_a_full_store_makes_the_sender_with_the_most_give_way);
  RUN_TEST(test_the_store_survives_a_reboot_and_drops_damaged_slots);
  RUN_TEST(test_a_failed_flash_write_stores_nothing);
  RUN_TEST(test_old_records_expire_but_records_without_a_time_stay);
  return UNITY_END();
}
