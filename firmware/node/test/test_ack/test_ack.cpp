// Golden vectors for signed acknowledgements (docs/control.md), on the host.
#include <string.h>
#include <unity.h>

#include "ack_vectors.h"
#include "stz_ack.h"
#include "stz_codec.h"

using namespace stz;

static CloudKey cloud_key() {
  CloudKey k;
  size_t n = 0;
  k.id = kCloudKeyId;
  TEST_ASSERT_EQUAL(Status::Ok, from_hex(kCloudPublicHex, strlen(kCloudPublicHex), k.pub, 32, n));
  return k;
}

void test_valid_acks_verify_and_carry_the_fields() {
  CloudKey k = cloud_key();
  for (const ValidAck& v : kValidAcks) {
    uint8_t raw[kAckLen];
    size_t n = 0;
    TEST_ASSERT_EQUAL_MESSAGE(Status::Ok, from_hex(v.hex, strlen(v.hex), raw, sizeof raw, n), v.name);
    Ack a;
    TEST_ASSERT_EQUAL_STRING_MESSAGE("ok", reason(verify_ack(raw, n, &k, 1, a)), v.name);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(v.node, a.node_id, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.key, a.key_id, v.name);
    TEST_ASSERT_EQUAL_UINT16_MESSAGE(v.max_seq, a.max_seq, v.name);
    TEST_ASSERT_EQUAL_UINT16_MESSAGE(v.mask, a.mask, v.name);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(v.at, a.signed_at, v.name);
    Ack b;
    TEST_ASSERT_EQUAL_STRING_MESSAGE("ok", reason(verify_ack_text(v.text, strlen(v.text), &k, 1, b)), v.name);
  }
}

void test_invalid_acks_are_rejected_with_the_stated_reason() {
  CloudKey k = cloud_key();
  for (const InvalidAck& v : kInvalidAcks) {
    Ack a;
    a.node_id = 0xdeadbeef;
    AckStatus s;
    if (v.text[0] != '\0') {
      s = verify_ack_text(v.text, strlen(v.text), &k, 1, a);
    } else {
      uint8_t raw[kAckLen + 8];
      size_t n = 0;
      TEST_ASSERT_EQUAL_MESSAGE(Status::Ok, from_hex(v.hex, strlen(v.hex), raw, sizeof raw, n), v.name);
      s = verify_ack(raw, n, &k, 1, a);
    }
    TEST_ASSERT_EQUAL_STRING_MESSAGE(v.reason, reason(s), v.name);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0xdeadbeef, a.node_id, v.name);  // output untouched on failure
  }
}

void test_a_trusted_key_set_without_the_cloud_key_rejects() {
  CloudKey other = cloud_key();
  other.id = 9;
  const ValidAck& v = kValidAcks[0];
  Ack a;
  TEST_ASSERT_EQUAL_STRING("unknown_cloud_key", reason(verify_ack_text(v.text, strlen(v.text), &other, 1, a)));
  TEST_ASSERT_EQUAL_STRING("unknown_cloud_key", reason(verify_ack_text(v.text, strlen(v.text), nullptr, 0, a)));
}

void test_a_wrong_public_key_never_verifies() {
  CloudKey k = cloud_key();
  k.pub[0] ^= 1;
  Ack a;
  for (const ValidAck& v : kValidAcks) {
    AckStatus s = verify_ack_text(v.text, strlen(v.text), &k, 1, a);
    TEST_ASSERT_TRUE_MESSAGE(s == AckStatus::BadSignature, v.name);
  }
}

void test_every_single_bit_flip_is_rejected() {
  CloudKey k = cloud_key();
  const ValidAck& v = kValidAcks[0];
  uint8_t raw[kAckLen];
  size_t n = 0;
  TEST_ASSERT_EQUAL(Status::Ok, from_hex(v.hex, strlen(v.hex), raw, sizeof raw, n));
  for (size_t bit = 0; bit < kAckLen * 8; bit++) {
    uint8_t copy[kAckLen];
    memcpy(copy, raw, kAckLen);
    copy[bit / 8] ^= (uint8_t)(1u << (bit % 8));
    Ack a;
    TEST_ASSERT_TRUE(verify_ack(copy, kAckLen, &k, 1, a) != AckStatus::Ok);
  }
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_valid_acks_verify_and_carry_the_fields);
  RUN_TEST(test_invalid_acks_are_rejected_with_the_stated_reason);
  RUN_TEST(test_a_trusted_key_set_without_the_cloud_key_rejects);
  RUN_TEST(test_a_wrong_public_key_never_verifies);
  RUN_TEST(test_every_single_bit_flip_is_rejected);
  return UNITY_END();
}
