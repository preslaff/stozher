// Signed acknowledgement verification (docs/control.md). Must accept every valid vector in
// tests/vectors/ack_messages.json and reject every invalid one with the stated reason.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace stz {

constexpr size_t kAckPayloadLen = 15;
constexpr size_t kAckLen = 79;  // payload + 64-byte Ed25519 signature
constexpr uint8_t kAckType = 0xA1;

enum class AckStatus : uint8_t { Ok, BadText, BadHex, BadLength, BadType, UnknownCloudKey, BadSignature };
const char* reason(AckStatus s);  // "bad_text", "bad_hex", ... the strings the API uses

struct Ack {
  uint8_t cloud_key_id = 0;
  uint32_t node_id = 0;
  uint8_t key_id = 0;
  uint16_t max_seq = 0;
  uint16_t mask = 0;
  uint32_t signed_at = 0;
};

struct CloudKey {
  uint8_t id;
  uint8_t pub[32];
};

// `raw` is the 79 bytes. `out` is written only when the result is Ok.
AckStatus verify_ack(const uint8_t* raw, size_t len, const CloudKey* keys, size_t n_keys, Ack& out);

// `text` is "STZA1:" + 158 hex characters (mesh form).
AckStatus verify_ack_text(const char* text, size_t text_len, const CloudKey* keys, size_t n_keys, Ack& out);

}  // namespace stz
