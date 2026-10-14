#include "stz_ubx.h"

#include <math.h>
#include <string.h>

namespace stz {
namespace ubx {

namespace {
constexpr uint8_t kSync1 = 0xB5, kSync2 = 0x62;
constexpr uint8_t kClsNav = 0x01, kIdPvt = 0x07, kIdStatus = 0x03, kIdSat = 0x35, kIdSvinfo = 0x30;
constexpr uint8_t kClsMon = 0x0A, kIdRf = 0x38, kIdHw = 0x09;
constexpr uint8_t kClsCfg = 0x06, kIdValset = 0x8A;
constexpr size_t kPvtLen = 92, kStatusLen = 16, kSatHeader = 8, kSatSv = 12, kRfHeader = 4, kRfBlock = 24;

uint16_t u2(const uint8_t* p) { return (uint16_t)(p[0] | p[1] << 8); }
uint32_t u4(const uint8_t* p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
int32_t i4(const uint8_t* p) { return (int32_t)u4(p); }
}  // namespace

void Reader::begin_frame() {
  ck_a_ = ck_b_ = 0;
  pos_ = 0;
  n_ = used_ = 0;
#ifndef STZ_NO_PER_SV
  svn_ = 0;
#endif
  sum_ = sumsq_ = 0;
}

void Reader::on_payload_byte(uint8_t b) {
  if (pos_ < kMaxPayload) buf_[pos_] = b;
  bool sat = cls_ == kClsNav && id_ == kIdSat;
  bool svinfo = cls_ == kClsNav && id_ == kIdSvinfo;
  if (sat || svinfo) {
    if (pos_ >= kSatHeader) {  // iTOW (the first four bytes) is already kept in buf_
      size_t k = (pos_ - kSatHeader) % kSatSv;
      sv_[k] = b;
      if (k == kSatSv - 1) {
        // NAV-SAT: cno at 2, svUsed is bit 3 of the flags at 8. NAV-SVINFO: flags at 2 (svUsed bit 0), cno at 4.
        uint8_t cno = sat ? sv_[2] : sv_[4];
        bool used = sat ? (sv_[8] & 0x08) != 0 : (sv_[2] & 0x01) != 0;
        if (cno > 0) {
          n_++;
          sum_ += cno;
          sumsq_ += (uint32_t)cno * cno;
          if (used) used_++;
#ifndef STZ_NO_PER_SV
          if (svn_ < kMaxSv) {
            svk_[svn_] = sat ? (uint16_t)(sv_[0] << 8 | sv_[1]) : (uint16_t)sv_[1];
            svc_[svn_++] = cno;
          }
#endif
        }
      }
    }
  }
}

Kind Reader::feed(uint8_t b) {
  auto sum = [&](uint8_t v) {
    ck_a_ = (uint8_t)(ck_a_ + v);
    ck_b_ = (uint8_t)(ck_b_ + ck_a_);
  };
  switch (state_) {
    case State::Sync1:
      if (b == kSync1) state_ = State::Sync2;
      return Kind::None;
    case State::Sync2:
      state_ = (b == kSync2) ? State::Class : (b == kSync1 ? State::Sync2 : State::Sync1);
      if (state_ == State::Class) begin_frame();
      return Kind::None;
    case State::Class:
      cls_ = b; sum(b); state_ = State::Id;
      return Kind::None;
    case State::Id:
      id_ = b; sum(b); state_ = State::LenLo;
      return Kind::None;
    case State::LenLo:
      len_ = b; sum(b); state_ = State::LenHi;
      return Kind::None;
    case State::LenHi:
      len_ = (uint16_t)(len_ | b << 8); sum(b);
      if (len_ > kMaxFrame) {
        state_ = State::Sync1;  // not a frame we ever subscribe to: treat as noise
      } else {
        state_ = (len_ == 0) ? State::CkA : State::Payload;
      }
      return Kind::None;
    case State::Payload:
      on_payload_byte(b);
      sum(b);
      if (++pos_ == len_) state_ = State::CkA;
      return Kind::None;
    case State::CkA:
      rx_ck_a_ = b; state_ = State::CkB;
      return Kind::None;
    case State::CkB:
      state_ = State::Sync1;
      if (rx_ck_a_ != ck_a_ || b != ck_b_) return Kind::BadChecksum;
      return finish();
  }
  return Kind::None;
}

Kind Reader::finish() {
  if (cls_ == kClsNav && id_ == kIdPvt && len_ == kPvtLen) {
    const uint8_t* p = buf_;
    Pvt v;
    v.itow_ms = u4(p);
    v.year = u2(p + 4);
    v.month = p[6]; v.day = p[7]; v.hour = p[8]; v.min = p[9]; v.sec = p[10];
    v.time_valid = (p[11] & 0x03) == 0x03;
    v.t_acc_ns = u4(p + 12);
    v.nano_ns = i4(p + 16);
    v.fix_type = p[20];
    v.fix_ok = (p[21] & 0x01) != 0;
    v.num_sv = p[23];
    v.lon_e7 = i4(p + 24);
    v.lat_e7 = i4(p + 28);
    v.h_acc_mm = u4(p + 40);
    v.g_speed_mm_s = i4(p + 60);
    pvt_ = v;
    return Kind::Pvt;
  }
  if (cls_ == kClsNav && id_ == kIdStatus && len_ == kStatusLen) {
    status_.itow_ms = u4(buf_);
    status_.spoof_state = (buf_[7] >> 3) & 0x03;
    return Kind::Status;
  }
  if (cls_ == kClsNav && (id_ == kIdSat || id_ == kIdSvinfo) && len_ >= kSatHeader &&
      (len_ - kSatHeader) % kSatSv == 0) {
    SatSummary s;
    s.itow_ms = u4(buf_);
    s.tracked = n_;
    s.used = used_;
    if (n_ > 0) {
      s.cno_mean = (uint8_t)((2u * sum_ + n_) / (2u * n_));
      float mean = (float)sum_ / n_;
      float var = (float)sumsq_ / n_ - mean * mean;
      float sd10 = 10.0f * sqrtf(var > 0 ? var : 0);
      s.cno_spread_x10 = sd10 >= 254.5f ? 255 : (uint8_t)(sd10 + 0.5f);
    }
#ifndef STZ_NO_PER_SV
    s.sv_n = svn_;
    for (uint8_t i = 0; i < svn_; i++) {
      s.sv_key[i] = svk_[i];
      s.sv_cno[i] = svc_[i];
    }
#endif
    sat_ = s;
    return Kind::Sat;
  }
  if (cls_ == kClsMon && id_ == kIdRf && len_ >= kRfHeader && (len_ - kRfHeader) % kRfBlock == 0) {
    RfSummary r;
    size_t blocks = (len_ - kRfHeader) / kRfBlock;
    if (blocks > (kMaxPayload - kRfHeader) / kRfBlock) blocks = (kMaxPayload - kRfHeader) / kRfBlock;
    for (size_t i = 0; i < blocks; i++) {
      const uint8_t* p = buf_ + kRfHeader + i * kRfBlock;
      uint8_t jam_state = p[1] & 0x03;
      uint16_t agc = u2(p + 14);
      uint16_t noise = u2(p + 12);
      if (jam_state > r.jam_state) r.jam_state = jam_state;
      if (p[16] > r.jam_ind) r.jam_ind = p[16];
      if (agc > r.agc) r.agc = agc;
      if (noise > r.noise) r.noise = noise;
    }
    rf_ = r;
    return Kind::Rf;
  }
  // NEO-6 / M8 MON-HW: 68 bytes on u-blox 6 (jamInd at 53), 60 on u-blox 7/8 (jamInd at 45).
  if (cls_ == kClsMon && id_ == kIdHw && (len_ == 60 || len_ == 68)) {
    RfSummary r;
    r.noise = u2(buf_ + 16);
    r.agc = u2(buf_ + 18);
    r.jam_state = (buf_[22] >> 2) & 0x03;
    r.jam_ind = buf_[len_ == 60 ? 45 : 53];
    rf_ = r;
    return Kind::Rf;
  }
  return Kind::Other;
}

uint32_t unix_time(const Pvt& p) {
  if (!p.time_valid || p.year < 1970) return 0;
  // days from civil (Howard Hinnant's algorithm)
  int y = p.year - (p.month <= 2);
  int era = y / 400;
  int yoe = y - era * 400;
  int doy = (153 * (p.month + (p.month > 2 ? -3 : 9)) + 2) / 5 + p.day - 1;
  int doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  int32_t days = era * 146097 + doe - 719468;
  return (uint32_t)days * 86400u + p.hour * 3600u + p.min * 60u + p.sec;
}

size_t build_valset(const ConfigItem* items, size_t n, uint8_t* out, size_t cap) {
  size_t payload = 4;
  for (size_t i = 0; i < n; i++) payload += 4 + items[i].size;
  size_t total = 6 + payload + 2;
  if (total > cap || payload > 0xFFFF) return 0;
  out[0] = kSync1; out[1] = kSync2; out[2] = kClsCfg; out[3] = kIdValset;
  out[4] = (uint8_t)payload; out[5] = (uint8_t)(payload >> 8);
  size_t o = 6;
  out[o++] = 0;     // version
  out[o++] = 0x01;  // layers: RAM only (the module has no flash)
  out[o++] = 0; out[o++] = 0;
  for (size_t i = 0; i < n; i++) {
    for (int b = 0; b < 4; b++) out[o++] = (uint8_t)(items[i].key >> (8 * b));
    for (int b = 0; b < items[i].size; b++) out[o++] = (uint8_t)(items[i].value >> (8 * b));
  }
  uint8_t a = 0, c = 0;
  for (size_t i = 2; i < o; i++) { a = (uint8_t)(a + out[i]); c = (uint8_t)(c + a); }
  out[o++] = a; out[o++] = c;
  return o;
}

// Key IDs from the u-blox configuration database, cross-checked against pyubx2's table.
const ConfigItem kBootConfig[] = {
    {0x10740002, 1, 0},     // CFG-UART1OUTPROT-NMEA off
    {0x10740001, 1, 1},     // CFG-UART1OUTPROT-UBX on
    {0x30210001, 2, 1000},  // CFG-RATE-MEAS 1000 ms
    {0x20910007, 1, 1},     // CFG-MSGOUT-UBX_NAV_PVT_UART1
    {0x20910016, 1, 1},     // CFG-MSGOUT-UBX_NAV_SAT_UART1
    {0x2091001b, 1, 1},     // CFG-MSGOUT-UBX_NAV_STATUS_UART1
    {0x2091035a, 1, 1},     // CFG-MSGOUT-UBX_MON_RF_UART1
};
const size_t kBootConfigCount = sizeof(kBootConfig) / sizeof(kBootConfig[0]);

size_t build_boot_config(uint8_t* out, size_t cap) { return build_valset(kBootConfig, kBootConfigCount, out, cap); }

#ifndef STZ_NO_PER_SV
namespace {
size_t frame(uint8_t cls, uint8_t id, const uint8_t* payload, uint8_t len, uint8_t* out, size_t cap) {
  size_t total = 6 + (size_t)len + 2;
  if (total > cap) return 0;
  out[0] = kSync1; out[1] = kSync2; out[2] = cls; out[3] = id; out[4] = len; out[5] = 0;
  for (uint8_t i = 0; i < len; i++) out[6 + i] = payload[i];
  uint8_t a = 0, c = 0;
  for (size_t i = 2; i < 6 + (size_t)len; i++) { a = (uint8_t)(a + out[i]); c = (uint8_t)(c + a); }
  out[6 + len] = a;
  out[7 + len] = c;
  return total;
}
}  // namespace

size_t build_cfg_rst(uint16_t nav_bbr_mask, uint8_t reset_mode, uint8_t* out, size_t cap) {
  const uint8_t p[4] = {(uint8_t)nav_bbr_mask, (uint8_t)(nav_bbr_mask >> 8), reset_mode, 0};
  return frame(kClsCfg, 0x04, p, sizeof p, out, cap);
}

size_t build_cfg_msg(uint8_t msg_class, uint8_t msg_id, uint8_t* out, size_t cap) {
  const uint8_t p[8] = {msg_class, msg_id, 0, 1, 0, 0, 0, 0};  // rate 1 on UART1 only
  return frame(kClsCfg, 0x01, p, sizeof p, out, cap);
}
#endif

}  // namespace ubx
}  // namespace stz
