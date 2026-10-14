// UBX parsing against frames built by pyubx2 (tools/gen_ubx_vectors.py), plus the boot configuration frame
// and the epoch assembly.
#include <string.h>
#include <unity.h>

#include "stz_codec.h"
#include "stz_gnss.h"
#include "stz_ubx.h"
#include "ubx_vectors.h"

using namespace stz;

static size_t unhex(const char* hex, uint8_t* out, size_t cap) {
  size_t n = 0;
  TEST_ASSERT_EQUAL(Status::Ok, from_hex(hex, strlen(hex), out, cap, n));
  return n;
}

// Feeds a hex stream; returns the kinds as letters (P pvt, S sat, T status, R rf, O other, X bad checksum).
static void feed_all(ubx::Reader& r, const char* hex, char* kinds, size_t cap) {
  static uint8_t buf[2048];
  size_t n = unhex(hex, buf, sizeof buf);
  size_t k = 0;
  for (size_t i = 0; i < n; i++) {
    ubx::Kind kind = r.feed(buf[i]);
    char c = 0;
    switch (kind) {
      case ubx::Kind::Pvt: c = 'P'; break;
      case ubx::Kind::Sat: c = 'S'; break;
      case ubx::Kind::Status: c = 'T'; break;
      case ubx::Kind::Rf: c = 'R'; break;
      case ubx::Kind::Other: c = 'O'; break;
      case ubx::Kind::BadChecksum: c = 'X'; break;
      case ubx::Kind::None: break;
    }
    if (c && k + 1 < cap) kinds[k++] = c;
  }
  kinds[k] = 0;
}

void test_nav_pvt_fields() {
  for (const PvtVec& v : kPvt) {
    ubx::Reader r;
    char kinds[8];
    feed_all(r, v.hex, kinds, sizeof kinds);
    TEST_ASSERT_EQUAL_STRING_MESSAGE("P", kinds, v.name);
    const ubx::Pvt& p = r.pvt();
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(v.itow, p.itow_ms, v.name);
    TEST_ASSERT_EQUAL_UINT16_MESSAGE(v.year, p.year, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.month, p.month, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.day, p.day, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.hour, p.hour, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.min, p.min, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.sec, p.sec, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.fix_type, p.fix_type, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.fix_ok, p.fix_ok, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.num_sv, p.num_sv, v.name);
    TEST_ASSERT_EQUAL_INT32_MESSAGE(v.lat_e7, p.lat_e7, v.name);
    TEST_ASSERT_EQUAL_INT32_MESSAGE(v.lon_e7, p.lon_e7, v.name);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(v.h_acc_mm, p.h_acc_mm, v.name);
    TEST_ASSERT_EQUAL_INT32_MESSAGE(v.g_speed_mm_s, p.g_speed_mm_s, v.name);
    TEST_ASSERT_EQUAL_INT32_MESSAGE(v.nano, p.nano_ns, v.name);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(v.t_acc_ns, p.t_acc_ns, v.name);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(v.unix_s, ubx::unix_time(p), v.name);
  }
}

void test_no_valid_time_gives_no_unix_time() {
  ubx::Reader r;
  char kinds[8];
  feed_all(r, kPvt[1].hex, kinds, sizeof kinds);  // pvt_no_fix has validTime 0
  TEST_ASSERT_FALSE(r.pvt().time_valid);
  TEST_ASSERT_EQUAL_UINT32(0, ubx::unix_time(r.pvt()));
}

void test_nav_sat_summary_including_a_message_longer_than_the_buffer() {
  for (const SatVec& v : kSat) {
    ubx::Reader r;
    char kinds[8];
    feed_all(r, v.hex, kinds, sizeof kinds);
    TEST_ASSERT_EQUAL_STRING_MESSAGE("S", kinds, v.name);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(v.itow, r.sat().itow_ms, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.tracked, r.sat().tracked, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.used, r.sat().used, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.mean, r.sat().cno_mean, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.spread10, r.sat().cno_spread_x10, v.name);
  }
}

void test_nav_status_spoofing_state() {
  for (const StatusVec& v : kStatus) {
    ubx::Reader r;
    char kinds[8];
    feed_all(r, v.hex, kinds, sizeof kinds);
    TEST_ASSERT_EQUAL_STRING_MESSAGE("T", kinds, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.spoof, r.status().spoof_state, v.name);
  }
}

void test_mon_rf_takes_the_worst_block() {
  for (const RfVec& v : kRf) {
    ubx::Reader r;
    char kinds[8];
    feed_all(r, v.hex, kinds, sizeof kinds);
    TEST_ASSERT_EQUAL_STRING_MESSAGE("R", kinds, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.jam_state, r.rf().jam_state, v.name);
    TEST_ASSERT_EQUAL_UINT16_MESSAGE(v.agc, r.rf().agc, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.jam_ind, r.rf().jam_ind, v.name);
    TEST_ASSERT_EQUAL_UINT16_MESSAGE(v.noise, r.rf().noise, v.name);
  }
}

void test_neo6_nav_svinfo_gives_the_same_summary_as_nav_sat() {
  for (const SatVec& v : kSvinfo) {
    ubx::Reader r;
    char kinds[8];
    feed_all(r, v.hex, kinds, sizeof kinds);
    TEST_ASSERT_EQUAL_STRING_MESSAGE("S", kinds, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.tracked, r.sat().tracked, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.used, r.sat().used, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.mean, r.sat().cno_mean, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.spread10, r.sat().cno_spread_x10, v.name);
  }
}

void test_neo6_mon_hw_in_both_lengths() {
  for (const RfVec& v : kHw) {
    ubx::Reader r;
    char kinds[8];
    feed_all(r, v.hex, kinds, sizeof kinds);
    TEST_ASSERT_EQUAL_STRING_MESSAGE("R", kinds, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.jam_state, r.rf().jam_state, v.name);
    TEST_ASSERT_EQUAL_UINT16_MESSAGE(v.agc, r.rf().agc, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.jam_ind, r.rf().jam_ind, v.name);
    TEST_ASSERT_EQUAL_UINT16_MESSAGE(v.noise, r.rf().noise, v.name);
  }
}

void test_noise_and_bad_frames_never_produce_a_message() {
  for (const StreamVec& v : kStreams) {
    ubx::Reader r;
    char kinds[16];
    feed_all(r, v.hex, kinds, sizeof kinds);
    TEST_ASSERT_EQUAL_STRING_MESSAGE(v.kinds, kinds, v.name);
  }
}

void test_a_bad_checksum_leaves_the_last_good_values() {
  ubx::Reader r;
  char kinds[8];
  feed_all(r, kPvt[0].hex, kinds, sizeof kinds);
  int32_t lat = r.pvt().lat_e7;
  uint8_t raw[128];
  size_t n = unhex(kPvt[2].hex, raw, sizeof raw);
  raw[30] ^= 0x40;  // corrupt the latitude of a different frame
  for (size_t i = 0; i < n; i++) TEST_ASSERT_TRUE(r.feed(raw[i]) != ubx::Kind::Pvt);
  TEST_ASSERT_EQUAL_INT32(lat, r.pvt().lat_e7);
}

void test_boot_configuration_frame_matches_pyubx2() {
  uint8_t expect[128], got[128];
  size_t n = unhex(kBootConfigHex, expect, sizeof expect);
  size_t m = ubx::build_boot_config(got, sizeof got);
  TEST_ASSERT_EQUAL(n, m);
  TEST_ASSERT_EQUAL_UINT8_ARRAY(expect, got, n);
  uint8_t tiny[10];
  TEST_ASSERT_EQUAL(0, ubx::build_boot_config(tiny, sizeof tiny));
}

static Epoch collect(const char* const* hexes, size_t count, int& epochs, EpochBuilder& b) {
  ubx::Reader r;
  Epoch out, last;
  epochs = 0;
  for (size_t h = 0; h < count; h++) {
    uint8_t raw[600];
    size_t n = unhex(hexes[h], raw, sizeof raw);
    for (size_t i = 0; i < n; i++) {
      ubx::Kind k = r.feed(raw[i]);
      if (k != ubx::Kind::None && b.add(k, r, 5000u + (uint32_t)h, out)) {
        epochs++;
        last = out;
      }
    }
  }
  return last;
}

void test_epoch_completes_when_pvt_sat_and_status_of_one_itow_are_in() {
  EpochBuilder b;
  const char* seq[] = {kRf[1].hex, kPvt[0].hex, kSat[0].hex, kStatus[2].hex};
  int epochs;
  Epoch e = collect(seq, 4, epochs, b);
  TEST_ASSERT_EQUAL(1, epochs);
  TEST_ASSERT_TRUE(e.fix_ok);
  TEST_ASSERT_FALSE(e.sat_missing);
  TEST_ASSERT_EQUAL_INT32(425000000, e.lat_e7);
  TEST_ASSERT_EQUAL_UINT32(1795775405u, e.utc_s);
  TEST_ASSERT_EQUAL_UINT8(1, e.sats_tracked);
  TEST_ASSERT_EQUAL_UINT8(40, e.cn0_mean);
  TEST_ASSERT_EQUAL_UINT8(2, e.spoof_state);
  TEST_ASSERT_EQUAL_UINT8(3, e.jam_state);  // MON-RF has no iTOW: the latest block is used
  TEST_ASSERT_EQUAL_UINT32(5003u, e.mono_ms);
}

void test_a_missing_nav_sat_is_flagged_and_flushed_by_the_next_epoch() {
  EpochBuilder b;
  const char* seq[] = {kPvt[0].hex, kStatus[0].hex, kPvt[3].hex};
  int epochs;
  Epoch e = collect(seq, 3, epochs, b);
  TEST_ASSERT_EQUAL(1, epochs);  // the iTOW 1000 epoch, flushed when iTOW 2000 arrived
  TEST_ASSERT_TRUE(e.sat_missing);
  TEST_ASSERT_EQUAL_UINT8(0, e.sats_tracked);
}

void test_a_lone_status_or_sat_without_position_makes_no_epoch() {
  EpochBuilder b;
  const char* seq[] = {kSat[0].hex, kStatus[0].hex, kSat[4].hex};
  int epochs;
  collect(seq, 3, epochs, b);
  TEST_ASSERT_EQUAL(0, epochs);
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_nav_pvt_fields);
  RUN_TEST(test_no_valid_time_gives_no_unix_time);
  RUN_TEST(test_nav_sat_summary_including_a_message_longer_than_the_buffer);
  RUN_TEST(test_neo6_nav_svinfo_gives_the_same_summary_as_nav_sat);
  RUN_TEST(test_nav_status_spoofing_state);
  RUN_TEST(test_neo6_mon_hw_in_both_lengths);
  RUN_TEST(test_mon_rf_takes_the_worst_block);
  RUN_TEST(test_noise_and_bad_frames_never_produce_a_message);
  RUN_TEST(test_a_bad_checksum_leaves_the_last_good_values);
  RUN_TEST(test_boot_configuration_frame_matches_pyubx2);
  RUN_TEST(test_epoch_completes_when_pvt_sat_and_status_of_one_itow_are_in);
  RUN_TEST(test_a_missing_nav_sat_is_flagged_and_flushed_by_the_next_epoch);
  RUN_TEST(test_a_lone_status_or_sat_without_position_makes_no_epoch);
  return UNITY_END();
}
