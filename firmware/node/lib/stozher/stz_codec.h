// Witness record codec (docs/packet.md). Must reproduce tests/vectors/witness_records.json bit for bit.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace stz {

constexpr size_t kCoreLen = 28;
constexpr size_t kExtLen = 32;
constexpr size_t kMaxLen = kExtLen;

enum MsgType : uint8_t { kHeartbeat = 0, kEvent = 1, kSummary = 2, kTest = 3 };
enum EventClass : uint8_t { kNormal = 0, kJamming = 1, kSpoofing = 2, kBenign = 3, kTamper = 4, kTimeAnomaly = 5, kUnknown = 7 };

// Stable reasons, the same strings the API uses (docs/packet.md "Rejection reasons").
enum class Status : uint8_t {
  Ok, BadLength, BadHex, BadVersion, BadType, BadReserved, LengthMismatch, BadTag, BadClass, BadCoordinates,
};
const char* reason(Status s);

struct Record {
  uint8_t type = kHeartbeat;
  uint16_t seq = 0;
  uint8_t key_id = 0;  // 0..15
  bool extended = false;
  uint8_t event_class = kNormal;
  uint8_t confidence = 0;  // 0..31
  uint8_t osnma = 0;       // 0..3
  bool stationary = false, rx_jamming = false, rx_spoofing = false, tamper = false, mesh = false;
  int32_t lat_e7 = 0, lon_e7 = 0;
  uint32_t timestamp = 0;
  uint16_t duration_s = 0;
  uint8_t cn0_mean = 0, cn0_drop = 0, cn0_spread = 0, agc = 0;
  uint8_t satellites = 0, pos_jump_code = 0, clock_jump_code = 0, battery = 255;  // extended only
};

struct Keys {
  uint8_t enc[16];
  uint8_t mac[32];
};

// k_enc, k_mac from the per-node master key (16 bytes) for one key ID.
void derive_keys(const uint8_t master[16], uint32_t node_id, uint8_t key_id, Keys& out);

// Writes 28 or 32 bytes (by `extended`); returns the length.
size_t encode(const Record& r, uint32_t node_id, const Keys& keys, uint8_t out[kMaxLen]);

// Verifies the tag before decrypting or using any field. The key ID in the header selects the keys.
Status decode(const uint8_t* raw, size_t len, uint32_t node_id, const uint8_t master[16], Record& out);

// Lowercase or uppercase hex in; `cap` bytes out at most. Returns BadHex for odd length or non-hex.
Status from_hex(const char* text, size_t text_len, uint8_t* out, size_t cap, size_t& out_len);
void to_hex(const uint8_t* data, size_t len, char* out);  // writes 2*len chars and a NUL

// Scaling helpers for the extended jump codes (docs/packet.md). Not used on the authenticated path.
uint8_t jump_code_from_units(float units);
float jump_units_from_code(uint8_t code);

}  // namespace stz
