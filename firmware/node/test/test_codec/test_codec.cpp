// Golden vectors for the witness record, on the host. Every valid vector must encode to the same bytes
// and decode to the same fields; every invalid one must be rejected with the stated reason.
#include <string.h>
#include <unity.h>

#include "stz_codec.h"
#include "stz_crypto.h"
#include "vectors.h"

using namespace stz;

static void hex_to_bytes(const char* hex, uint8_t* out, size_t cap, size_t& n) {
  TEST_ASSERT_EQUAL(Status::Ok, from_hex(hex, strlen(hex), out, cap, n));
}

static Record record_of(const ValidVector& v) {
  Record r;
  r.type = v.type; r.seq = v.seq; r.key_id = v.key_id; r.extended = v.extended;
  r.event_class = v.event_class; r.confidence = v.confidence; r.osnma = v.osnma;
  r.stationary = v.stationary; r.rx_jamming = v.rx_jamming; r.rx_spoofing = v.rx_spoofing;
  r.tamper = v.tamper; r.mesh = v.mesh; r.lat_e7 = v.lat_e7; r.lon_e7 = v.lon_e7;
  r.timestamp = v.timestamp; r.duration_s = v.duration_s;
  r.cn0_mean = v.cn0_mean; r.cn0_drop = v.cn0_drop; r.cn0_spread = v.cn0_spread; r.agc = v.agc;
  if (v.extended) { r.satellites = v.satellites; r.pos_jump_code = v.pos_jump_code; r.clock_jump_code = v.clock_jump_code; r.battery = v.battery; }
  return r;
}

void test_sha256_and_hmac_known_answers() {
  uint8_t out[32], expect[32];
  size_t n;
  Sha256 s; s.begin(); s.update((const uint8_t*)"abc", 3); s.finish(out);
  hex_to_bytes("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", expect, 32, n);
  TEST_ASSERT_EQUAL_UINT8_ARRAY(expect, out, 32);
  // RFC 4231 test case 2
  hmac_sha256((const uint8_t*)"Jefe", 4, (const uint8_t*)"what do ya want for nothing?", 28, out);
  hex_to_bytes("5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843", expect, 32, n);
  TEST_ASSERT_EQUAL_UINT8_ARRAY(expect, out, 32);
  // multi-block input
  uint8_t big[200];
  memset(big, 'a', sizeof big);
  s.begin(); s.update(big, 100); s.update(big + 100, 100); s.finish(out);
  uint8_t whole[32];
  s.begin(); s.update(big, 200); s.finish(whole);
  TEST_ASSERT_EQUAL_UINT8_ARRAY(whole, out, 32);
}

void test_hkdf_rfc5869_case_1() {
  uint8_t ikm[22], salt[13], info[10], expect[32], out[32];
  size_t n;
  hex_to_bytes("0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b", ikm, 22, n);
  hex_to_bytes("000102030405060708090a0b0c", salt, 13, n);
  hex_to_bytes("f0f1f2f3f4f5f6f7f8f9", info, 10, n);
  TEST_ASSERT_TRUE(hkdf_sha256(ikm, 22, salt, 13, info, 10, out, 32));
  hex_to_bytes("3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf", expect, 32, n);
  TEST_ASSERT_EQUAL_UINT8_ARRAY(expect, out, 32);  // first 32 of the 42 bytes in the RFC
}

void test_aes128_fips197_vector() {
  uint8_t key[16], in[16], expect[16], out[16];
  size_t n;
  hex_to_bytes("000102030405060708090a0b0c0d0e0f", key, 16, n);
  hex_to_bytes("00112233445566778899aabbccddeeff", in, 16, n);
  hex_to_bytes("69c4e0d86a7b0430d8cdb78070b4c55a", expect, 16, n);
  Aes128 aes; aes.set_key(key); aes.encrypt_block(in, out);
  TEST_ASSERT_EQUAL_UINT8_ARRAY(expect, out, 16);
}

void test_valid_vectors_encode_and_decode_bit_for_bit() {
  for (const ValidVector& v : kValid) {
    uint8_t master[16], expect[kMaxLen], out[kMaxLen];
    size_t n, m;
    hex_to_bytes(v.master, master, 16, n);
    hex_to_bytes(v.hex, expect, kMaxLen, m);
    Keys keys;
    derive_keys(master, v.node_id, v.key_id, keys);
    Record r = record_of(v);
    size_t len = encode(r, v.node_id, keys, out);
    TEST_ASSERT_EQUAL_MESSAGE(m, len, v.name);
    TEST_ASSERT_EQUAL_UINT8_ARRAY_MESSAGE(expect, out, m, v.name);

    Record d;
    TEST_ASSERT_EQUAL_MESSAGE((int)Status::Ok, (int)decode(expect, m, v.node_id, master, d), v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.type, d.type, v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.seq, d.seq, v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.key_id, d.key_id, v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.extended, d.extended, v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.event_class, d.event_class, v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.confidence, d.confidence, v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.osnma, d.osnma, v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.stationary, d.stationary, v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.rx_jamming, d.rx_jamming, v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.rx_spoofing, d.rx_spoofing, v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.tamper, d.tamper, v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.mesh, d.mesh, v.name);
    TEST_ASSERT_EQUAL_INT32_MESSAGE(r.lat_e7, d.lat_e7, v.name);
    TEST_ASSERT_EQUAL_INT32_MESSAGE(r.lon_e7, d.lon_e7, v.name);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(r.timestamp, d.timestamp, v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.duration_s, d.duration_s, v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.cn0_mean, d.cn0_mean, v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.cn0_drop, d.cn0_drop, v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.cn0_spread, d.cn0_spread, v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.agc, d.agc, v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.satellites, d.satellites, v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.pos_jump_code, d.pos_jump_code, v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.clock_jump_code, d.clock_jump_code, v.name);
    TEST_ASSERT_EQUAL_MESSAGE(r.battery, d.battery, v.name);
  }
}

void test_invalid_vectors_are_rejected_with_the_stated_reason() {
  for (const InvalidVector& v : kInvalid) {
    uint8_t master[16] = {0}, raw[kMaxLen + 4];
    size_t n = 0, len = 0;
    if (v.master[0]) hex_to_bytes(v.master, master, 16, n);
    Status s = from_hex(v.hex, strlen(v.hex), raw, sizeof raw, len);
    if (s == Status::Ok) {
      Record d;
      s = decode(raw, len, v.node_id, master, d);
    }
    TEST_ASSERT_EQUAL_STRING_MESSAGE(v.reason, reason(s), v.name);
  }
}

void test_a_forged_record_never_reaches_the_fields() {
  const ValidVector& v = kValid[0];
  uint8_t master[16], raw[kMaxLen];
  size_t n, len;
  hex_to_bytes(v.master, master, 16, n);
  hex_to_bytes(v.hex, raw, kMaxLen, len);
  for (size_t i = 0; i < len; i++) {  // flipping any single bit anywhere breaks the tag or the header
    for (int bit = 0; bit < 8; bit++) {
      uint8_t copy[kMaxLen];
      memcpy(copy, raw, len);
      copy[i] ^= (uint8_t)(1 << bit);
      Record d;
      d.lat_e7 = 12345;
      Status s = decode(copy, len, v.node_id, master, d);
      TEST_ASSERT_NOT_EQUAL(Status::Ok, s);
      TEST_ASSERT_EQUAL_INT32(12345, d.lat_e7);  // the output record was left untouched
    }
  }
}

void test_hex_helpers() {
  uint8_t out[4];
  size_t n;
  TEST_ASSERT_EQUAL(Status::Ok, from_hex("0aFF10\n", 7, out, 4, n));
  TEST_ASSERT_EQUAL(3, n);
  TEST_ASSERT_EQUAL(Status::BadHex, from_hex("abc", 3, out, 4, n));
  TEST_ASSERT_EQUAL(Status::BadHex, from_hex("zz", 2, out, 4, n));
  TEST_ASSERT_EQUAL(Status::BadLength, from_hex("0102030405", 10, out, 4, n));
  char text[7];
  to_hex(out, 3, text);
  TEST_ASSERT_EQUAL_STRING("0aff10", text);
}

void test_jump_code_scaling_matches_the_documented_formula() {
  TEST_ASSERT_EQUAL(0, jump_code_from_units(0));
  TEST_ASSERT_EQUAL(24, jump_code_from_units(7.0f));  // 8 * log2(8)
  TEST_ASSERT_EQUAL(255, jump_code_from_units(1e30f));
  TEST_ASSERT_FLOAT_WITHIN(0.01f, 7.0f, jump_units_from_code(24));
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_sha256_and_hmac_known_answers);
  RUN_TEST(test_hkdf_rfc5869_case_1);
  RUN_TEST(test_aes128_fips197_vector);
  RUN_TEST(test_valid_vectors_encode_and_decode_bit_for_bit);
  RUN_TEST(test_invalid_vectors_are_rejected_with_the_stated_reason);
  RUN_TEST(test_a_forged_record_never_reaches_the_fields);
  RUN_TEST(test_hex_helpers);
  RUN_TEST(test_jump_code_scaling_matches_the_documented_formula);
  return UNITY_END();
}
