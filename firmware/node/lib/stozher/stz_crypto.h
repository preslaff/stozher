// Portable SHA-256, HMAC-SHA256, HKDF-SHA256 and AES-128 for the witness record (docs/packet.md).
// No dynamic allocation, no exceptions, no platform headers: the same code runs on the host for
// the golden vectors and on the ESP32-S3. A hardware (mbedTLS) backend can replace it later behind
// the same functions without touching the codec.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace stz {

struct Sha256 {
  uint32_t h[8];
  uint8_t buf[64];
  uint64_t bytes;
  size_t fill;
  void begin();
  void update(const uint8_t* data, size_t len);
  void finish(uint8_t out[32]);
};

void hmac_sha256(const uint8_t* key, size_t key_len, const uint8_t* msg, size_t msg_len, uint8_t out[32]);

// HKDF-SHA256 for a single output block (len <= 32), which is all the packet format needs.
bool hkdf_sha256(const uint8_t* ikm, size_t ikm_len, const uint8_t* salt, size_t salt_len,
                 const uint8_t* info, size_t info_len, uint8_t* out, size_t len);

struct Aes128 {
  uint8_t round_keys[176];
  void set_key(const uint8_t key[16]);
  void encrypt_block(const uint8_t in[16], uint8_t out[16]) const;
};

// Constant-time comparison: true when equal.
bool ct_equal(const uint8_t* a, const uint8_t* b, size_t len);

}  // namespace stz
