#include "stz_gnss.h"

namespace stz {

void EpochBuilder::clear() { have_pvt_ = have_sat_ = have_status_ = false; }

bool EpochBuilder::flush(uint32_t now_ms, Epoch& out) {
  if (!have_pvt_) {  // nothing positional to report for this iTOW
    clear();
    return false;
  }
  Epoch e;
  e.mono_ms = now_ms;
  e.utc_s = ubx::unix_time(pvt_);
  e.fix_ok = pvt_.fix_ok && pvt_.fix_type >= 2;
  e.num_sv = pvt_.num_sv;
  e.lat_e7 = pvt_.lat_e7;
  e.lon_e7 = pvt_.lon_e7;
  e.h_acc_mm = pvt_.h_acc_mm;
  e.speed_mm_s = pvt_.g_speed_mm_s;
  if (have_sat_) {
    e.sats_tracked = sat_.tracked;
    e.sats_used = sat_.used;
    e.cn0_mean = sat_.cno_mean;
    e.cn0_spread_x10 = sat_.cno_spread_x10;
  } else {
    e.sat_missing = true;
  }
  e.spoof_state = have_status_ ? status_.spoof_state : 0;
  e.jam_state = rf_.jam_state;
  e.jam_ind = rf_.jam_ind;
  out = e;
  clear();
  return true;
}

bool EpochBuilder::add(ubx::Kind kind, const ubx::Reader& r, uint32_t now_ms, Epoch& out) {
  if (kind == ubx::Kind::Rf) {
    rf_ = r.rf();
    return false;
  }
  uint32_t itow;
  switch (kind) {
    case ubx::Kind::Pvt: itow = r.pvt().itow_ms; break;
    case ubx::Kind::Sat: itow = r.sat().itow_ms; break;
    case ubx::Kind::Status: itow = r.status().itow_ms; break;
    default: return false;
  }
  bool flushed = false;
  if ((have_pvt_ || have_sat_ || have_status_) && itow != itow_) flushed = flush(now_ms, out);
  itow_ = itow;
  if (kind == ubx::Kind::Pvt) { pvt_ = r.pvt(); have_pvt_ = true; }
  if (kind == ubx::Kind::Sat) { sat_ = r.sat(); have_sat_ = true; }
  if (kind == ubx::Kind::Status) { status_ = r.status(); have_status_ = true; }
  if (flushed) return true;  // the new message starts the next epoch; it cannot complete it as well
  if (have_pvt_ && have_sat_ && have_status_) return flush(now_ms, out);
  return false;
}

}  // namespace stz
