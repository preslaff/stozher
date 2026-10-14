#include "stz_ack.h"

#include <string.h>

#include "stz_codec.h"
extern "C" {
#include "tweetnacl.h"
}

// TweetNaCl links against randombytes for key generation, which the node never does. Trap if reached.
extern "C" void randombytes(unsigned char*, unsigned long long) { __builtin_trap(); }

namespace stz {

namespace {
const char kContext[] = "stozher/v1/ack";
constexpr size_t kContextLen = sizeof(kContext) - 1;
constexpr size_t kSigLen = 64;
constexpr char kTextPrefix[] = "STZA1:";
constexpr size_t kPrefixLen = sizeof(kTextPrefix) - 1;
}  // namespace

const char* reason(AckStatus s) {
  switch (s) {
    case AckStatus::Ok: return "ok";
    case AckStatus::BadText: return "bad_text";
    case AckStatus::BadHex: return "bad_hex";
    case AckStatus::BadLength: return "bad_length";
    case AckStatus::BadType: return "bad_type";
    case AckStatus::UnknownCloudKey: return "unknown_cloud_key";
    case AckStatus::BadSignature: return "bad_signature";
  }
  return "bad_signature";
}

AckStatus verify_ack(const uint8_t* raw, size_t len, const CloudKey* keys, size_t n_keys, Ack& out) {
  if (len != kAckLen) return AckStatus::BadLength;
  if (raw[0] != kAckType) return AckStatus::BadType;
  const CloudKey* key = nullptr;
  for (size_t i = 0; i < n_keys; i++)
    if (keys[i].id == raw[1]) key = &keys[i];
  if (key == nullptr) return AckStatus::UnknownCloudKey;

  // crypto_sign_open wants signature || message, and writes the message back out.
  uint8_t sm[kSigLen + kContextLen + kAckPayloadLen];
  uint8_t m[sizeof sm];
  unsigned long long mlen = 0;
  memcpy(sm, raw + kAckPayloadLen, kSigLen);
  memcpy(sm + kSigLen, kContext, kContextLen);
  memcpy(sm + kSigLen + kContextLen, raw, kAckPayloadLen);
  if (crypto_sign_open(m, &mlen, sm, sizeof sm, key->pub) != 0) return AckStatus::BadSignature;

  out.cloud_key_id = raw[1];
  out.node_id = (uint32_t)raw[2] << 24 | (uint32_t)raw[3] << 16 | (uint32_t)raw[4] << 8 | raw[5];
  out.key_id = raw[6];
  out.max_seq = (uint16_t)(raw[7] << 8 | raw[8]);
  out.mask = (uint16_t)(raw[9] << 8 | raw[10]);
  out.signed_at = (uint32_t)raw[11] << 24 | (uint32_t)raw[12] << 16 | (uint32_t)raw[13] << 8 | raw[14];
  return AckStatus::Ok;
}

AckStatus verify_ack_text(const char* text, size_t text_len, const CloudKey* keys, size_t n_keys, Ack& out) {
  if (text_len < kPrefixLen || memcmp(text, kTextPrefix, kPrefixLen) != 0) return AckStatus::BadText;
  uint8_t raw[kAckLen + 1];
  size_t n = 0;
  Status s = from_hex(text + kPrefixLen, text_len - kPrefixLen, raw, sizeof raw, n);
  if (s == Status::BadHex) return AckStatus::BadHex;
  if (s != Status::Ok) return AckStatus::BadLength;
  return verify_ack(raw, n, keys, n_keys, out);
}

}  // namespace stz
