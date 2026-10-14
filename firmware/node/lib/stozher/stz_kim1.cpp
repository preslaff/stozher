#include "stz_kim1.h"

#include <string.h>

namespace stz {
namespace kim1 {

const char* reason(CmdStatus s) {
  switch (s) {
    case CmdStatus::Ok: return "ok";
    case CmdStatus::BadBand: return "bad_band";
    case CmdStatus::BadFreq: return "bad_freq";
    case CmdStatus::BadPower: return "bad_power";
    case CmdStatus::BadData: return "bad_data";
    case CmdStatus::BufferTooSmall: return "buffer_too_small";
  }
  return "bad_data";
}

CmdStatus build_tx(const Params& p, const uint8_t* data, size_t len, char* out, size_t cap, size_t& n) {
  if (p.band[0] != 'B' || p.band[1] < '1' || p.band[1] > '9' || p.band[2] != 0) return CmdStatus::BadBand;
  bool wide = p.band[1] == '4' || p.band[1] == '5';  // B4 and B5 span 0..700
  if (p.freq > (wide ? 700 : 600)) return CmdStatus::BadFreq;
  if (p.power_mw != 250 && p.power_mw != 500 && p.power_mw != 750 && p.power_mw != 1000 && p.power_mw != 1500)
    return CmdStatus::BadPower;
  if (len == 0 || len > kMaxDataBytes) return CmdStatus::BadData;
  size_t need = 6 + 2 + 1 + 3 + 1 + 4 + 1 + 2 * len + 2;  // "AT+TX=" band "," freq "," power "," hex CR LF
  if (cap < need) return CmdStatus::BufferTooSmall;

  size_t o = 0;
  auto put = [&](char c) { out[o++] = c; };
  auto put_num = [&](uint16_t v) {
    char tmp[5];
    int k = 0;
    do { tmp[k++] = (char)('0' + v % 10); v = (uint16_t)(v / 10); } while (v);
    while (k) put(tmp[--k]);
  };
  const char* head = "AT+TX=";
  for (const char* c = head; *c; c++) put(*c);
  put(p.band[0]); put(p.band[1]); put(',');
  put_num(p.freq); put(',');
  put_num(p.power_mw); put(',');
  static const char hex[] = "0123456789ABCDEF";
  for (size_t i = 0; i < len; i++) { put(hex[data[i] >> 4]); put(hex[data[i] & 15]); }
  put('\r'); put('\n');
  n = o;
  return CmdStatus::Ok;
}

Line LineParser::feed(uint8_t b) {
  if (b == '\r' || b == '\n') {
    Line result = Line::None;
    if (len_ > 0 && !overflow_) {
      buf_[len_] = 0;
      static const char kPrefix[] = "+TX_INFO:";
      if (strncmp(buf_, kPrefix, sizeof(kPrefix) - 1) == 0) {
        const char* v = buf_ + sizeof(kPrefix) - 1;
        while (*v == ' ') v++;
        if (v[0] == '1' && v[1] == 0) result = Line::TxDone;
        else if (v[0] == '0' && v[1] == 0) result = Line::TxFail;
        else result = Line::Other;
      } else {
        result = Line::Other;
      }
    } else if (len_ > 0) {
      result = Line::Other;
    }
    len_ = 0;
    overflow_ = false;
    return result;
  }
  if ((size_t)len_ + 1 < sizeof buf_) buf_[len_++] = (char)b;
  else overflow_ = true;
  return Line::None;
}

}  // namespace kim1
}  // namespace stz
