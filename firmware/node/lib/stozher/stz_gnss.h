// One second of GNSS features, assembled from the UBX messages of that epoch, and the sensor inputs the
// detector needs. Pure data: no hardware access.
#pragma once
#include <stdint.h>

#include "stz_ubx.h"

namespace stz {

struct Epoch {
  uint32_t mono_ms = 0;  // node clock when the epoch was completed (not GNSS time)
  uint32_t utc_s = 0;    // GNSS time as Unix seconds; 0 when the receiver has no valid time
  bool fix_ok = false;
  uint8_t num_sv = 0;
  int32_t lat_e7 = 0, lon_e7 = 0;
  uint32_t h_acc_mm = 0;
  int32_t speed_mm_s = 0;
  uint8_t sats_tracked = 0, sats_used = 0;
  uint8_t cn0_mean = 0, cn0_spread_x10 = 0;
  uint8_t jam_state = 0, jam_ind = 0;
#ifndef STZ_NO_PER_SV
  uint16_t noise = 0, agc = 0;  // receiver noise floor (noisePerMS) and AGC count: they rise under jamming, not under a cover
#endif
  uint8_t spoof_state = 0;
#ifndef STZ_NO_PER_SV
  uint8_t sv_n = 0;  // tracked signals one by one, as in ubx::SatSummary
  uint16_t sv_key[ubx::kMaxSv] = {};
  uint8_t sv_cno[ubx::kMaxSv] = {};
#endif
  bool sat_missing = false;  // no NAV-SAT of this epoch arrived: the C/N0 features are not trustworthy
  // From the node, not the receiver:
  bool blanked = false;       // own transmission in progress or just finished
  bool sensor_stationary = false;
  bool sensor_moving = false;  // accelerometer sees motion
  bool sensor_tamper = false;  // enclosure opened or shock
};

// Collects NAV-PVT, NAV-SAT and NAV-STATUS by iTOW (MON-RF has no iTOW and is used as last seen). An epoch is
// complete when all three of one iTOW are in, or when a message of a later iTOW arrives and the older one is
// flushed with what it has (sat_missing set if NAV-SAT never came).
class EpochBuilder {
 public:
  // Feed what the Reader just returned; true when `out` holds a finished epoch.
  bool add(ubx::Kind kind, const ubx::Reader& r, uint32_t now_ms, Epoch& out);

 private:
  bool flush(uint32_t now_ms, Epoch& out);
  void clear();

  bool have_pvt_ = false, have_sat_ = false, have_status_ = false;
  uint32_t itow_ = 0;
  ubx::Pvt pvt_;
  ubx::SatSummary sat_;
  ubx::Status status_;
  ubx::RfSummary rf_;
};

}  // namespace stz
