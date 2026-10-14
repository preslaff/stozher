#include "stz_codec.h"

#include <math.h>
#include <string.h>

#include "stz_crypto.h"

namespace stz {

const char* reason(Status s) {
  switch (s) {
    case Status::Ok: return "ok";
    case Status::BadLength: return "bad_length";
    case Status::BadHex: return "bad_hex";
    case Status::BadVersion: return "bad_version";
    case Status::BadType: return "bad_type";
    case Status::BadReserved: return "bad_reserved";
    case Status::LengthMismatch: return "length_mismatch";
    case Status::BadTag: return "bad_tag";
    case Status::BadClass: return "bad_class";
    case Status::BadCoordinates: return "bad_coordinates";
  }
  return "unknown";
}

static void put_u32(uint8_t* p, uint32_t v) {
  p[0] = (uint8_t)(v >> 24); p[1] = (uint8_t)(v >> 16); p[2] = (uint8_t)(v >> 8); p[3] = (uint8_t)v;
}
static uint32_t get_u32(const uint8_t* p) {
  return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}

void derive_keys(const uint8_t master[16], uint32_t node_id, uint8_t key_id, Keys& out) {
  uint8_t salt[4];
  put_u32(salt, node_id);
  uint8_t info[15];  // "stozher/v1/enc" (14 ASCII bytes) then the key id
  memcpy(info, "stozher/v1/enc", 14);
  info[14] = key_id;
  hkdf_sha256(master, 16, salt, 4, info, 15, out.enc, 16);
  memcpy(info, "stozher/v1/mac", 14);
  hkdf_sha256(master, 16, salt, 4, info, 15, out.mac, 32);
}

// AES-128 in counter mode; the same call encrypts and decrypts. At most 24 bytes (two blocks).
static void crypt(const uint8_t enc_key[16], uint32_t node_id, uint16_t seq, const uint8_t* in, size_t len, uint8_t* out) {
  Aes128 aes;
  aes.set_key(enc_key);
  for (size_t off = 0, block = 0; off < len; off += 16, block++) {
    uint8_t ctr[16] = {0}, ks[16];
    put_u32(ctr, node_id);
    ctr[4] = (uint8_t)(seq >> 8); ctr[5] = (uint8_t)seq;
    put_u32(ctr + 12, (uint32_t)block);
    aes.encrypt_block(ctr, ks);
    for (size_t i = 0; i < 16 && off + i < len; i++) out[off + i] = in[off + i] ^ ks[i];
  }
}

static void tag_of(const uint8_t mac_key[32], uint32_t node_id, const uint8_t header[4], const uint8_t* ct, size_t ct_len, uint8_t tag[4]) {
  uint8_t msg[8 + 24];
  put_u32(msg, node_id);
  memcpy(msg + 4, header, 4);
  memcpy(msg + 8, ct, ct_len);
  uint8_t full[32];
  hmac_sha256(mac_key, 32, msg, 8 + ct_len, full);
  memcpy(tag, full, 4);
}

size_t encode(const Record& r, uint32_t node_id, const Keys& keys, uint8_t out[kMaxLen]) {
  uint8_t header[4] = {(uint8_t)((1 << 4) | (r.type & 0x0F)), (uint8_t)(r.seq >> 8), (uint8_t)r.seq,
                       (uint8_t)((r.key_id << 4) | (r.extended ? 0x08 : 0))};
  uint8_t body[24];
  body[0] = (uint8_t)((r.event_class << 5) | (r.confidence & 0x1F));
  body[1] = (uint8_t)((r.osnma << 6) | (r.stationary << 5) | (r.rx_jamming << 4) | (r.rx_spoofing << 3) | (r.tamper << 2) | (r.mesh << 1));
  put_u32(body + 2, (uint32_t)r.lat_e7);
  put_u32(body + 6, (uint32_t)r.lon_e7);
  put_u32(body + 10, r.timestamp);
  body[14] = (uint8_t)(r.duration_s >> 8); body[15] = (uint8_t)r.duration_s;
  body[16] = r.cn0_mean; body[17] = r.cn0_drop; body[18] = r.cn0_spread; body[19] = r.agc;
  size_t body_len = 20;
  if (r.extended) {
    body[20] = r.satellites; body[21] = r.pos_jump_code; body[22] = r.clock_jump_code; body[23] = r.battery;
    body_len = 24;
  }
  memcpy(out, header, 4);
  crypt(keys.enc, node_id, r.seq, body, body_len, out + 4);
  tag_of(keys.mac, node_id, header, out + 4, body_len, out + 4 + body_len);
  return 4 + body_len + 4;
}

Status decode(const uint8_t* raw, size_t len, uint32_t node_id, const uint8_t master[16], Record& out) {
  if (len != kCoreLen && len != kExtLen) return Status::BadLength;
  const uint8_t* header = raw;
  if ((header[0] >> 4) != 1) return Status::BadVersion;
  if ((header[0] & 0x0F) > kTest) return Status::BadType;
  if (header[3] & 0x07) return Status::BadReserved;
  bool extended = (header[3] & 0x08) != 0;
  if (extended != (len == kExtLen)) return Status::LengthMismatch;

  size_t body_len = len - 8;
  const uint8_t* ct = raw + 4;
  uint8_t key_id = header[3] >> 4;
  Keys keys;
  derive_keys(master, node_id, key_id, keys);
  uint8_t tag[4];
  tag_of(keys.mac, node_id, header, ct, body_len, tag);
  if (!ct_equal(tag, raw + len - 4, 4)) return Status::BadTag;  // nothing below runs on unauthenticated data

  uint16_t seq = (uint16_t)(header[1] << 8 | header[2]);
  uint8_t body[24];
  crypt(keys.enc, node_id, seq, ct, body_len, body);
  uint8_t cls = body[0] >> 5;
  if (cls == 6) return Status::BadClass;
  if (body[1] & 0x01) return Status::BadReserved;
  int32_t lat = (int32_t)get_u32(body + 2), lon = (int32_t)get_u32(body + 6);
  if (lat > 900000000 || lat < -900000000 || lon > 1800000000 || lon < -1800000000) return Status::BadCoordinates;

  Record r;
  r.type = header[0] & 0x0F;
  r.seq = seq;
  r.key_id = key_id;
  r.extended = extended;
  r.event_class = cls;
  r.confidence = body[0] & 0x1F;
  r.osnma = body[1] >> 6;
  r.stationary = body[1] & 0x20; r.rx_jamming = body[1] & 0x10; r.rx_spoofing = body[1] & 0x08;
  r.tamper = body[1] & 0x04; r.mesh = body[1] & 0x02;
  r.lat_e7 = lat; r.lon_e7 = lon;
  r.timestamp = get_u32(body + 10);
  r.duration_s = (uint16_t)(body[14] << 8 | body[15]);
  r.cn0_mean = body[16]; r.cn0_drop = body[17]; r.cn0_spread = body[18]; r.agc = body[19];
  if (extended) {
    r.satellites = body[20]; r.pos_jump_code = body[21]; r.clock_jump_code = body[22]; r.battery = body[23];
  }
  out = r;
  return Status::Ok;
}

static int nibble(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

Status from_hex(const char* text, size_t text_len, uint8_t* out, size_t cap, size_t& out_len) {
  while (text_len > 0 && (text[text_len - 1] == '\n' || text[text_len - 1] == '\r' || text[text_len - 1] == ' ')) text_len--;
  out_len = 0;
  if (text_len % 2 != 0) return Status::BadHex;
  if (text_len / 2 > cap) return Status::BadLength;
  for (size_t i = 0; i < text_len; i += 2) {
    int hi = nibble(text[i]), lo = nibble(text[i + 1]);
    if (hi < 0 || lo < 0) return Status::BadHex;
    out[i / 2] = (uint8_t)(hi << 4 | lo);
  }
  out_len = text_len / 2;
  return Status::Ok;
}

void to_hex(const uint8_t* data, size_t len, char* out) {
  static const char digits[] = "0123456789abcdef";
  for (size_t i = 0; i < len; i++) { out[2 * i] = digits[data[i] >> 4]; out[2 * i + 1] = digits[data[i] & 15]; }
  out[2 * len] = '\0';
}

uint8_t jump_code_from_units(float units) {
  if (units <= 0) return 0;
  float c = roundf(8.0f * logf(1.0f + units) * 1.44269504f);
  return c > 255.0f ? 255 : (uint8_t)c;
}

float jump_units_from_code(uint8_t code) { return powf(2.0f, code / 8.0f) - 1.0f; }

}  // namespace stz
