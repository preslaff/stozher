// On-node detection rules J1, S1-S5, T1 (STOZHER Technical Specification, section 4). Deterministic and
// allocation-free: the same input epochs always give the same verdicts. Thresholds are initial values and
// are calibrated during the weekend. Cross-receiver checks run in the cloud, not here.
#pragma once
#include <stdint.h>

#include "stz_codec.h"
#include "stz_gnss.h"

namespace stz {

enum Rule : uint8_t {
  kRuleJ1 = 1 << 0,  // C/N0 drop against the rolling baseline, or receiver jamming indicator
  kRuleS1 = 1 << 1,  // fixed node: position away from the surveyed one while stationary
  kRuleS2 = 1 << 2,  // unusually uniform C/N0 (suspicion)
  kRuleS3 = 1 << 3,  // receiver spoofing indicator
  kRuleS4 = 1 << 4,  // clock or time jump
  kRuleS5 = 1 << 5,  // implausible motion, or motion that disagrees with the accelerometer
  kRuleT1 = 1 << 6,  // enclosure opened or shock
};

struct DetectConfig {
  bool fixed_node = false;
  int32_t surveyed_lat_e7 = 0, surveyed_lon_e7 = 0;
  float pos_dev_m = 50.0f;           // S1
  uint8_t pos_dev_epochs = 5;        // S1
  float cn0_drop_db = 6.0f;          // J1
  uint8_t cn0_drop_epochs = 3;       // J1
  float cn0_clear_db = 3.0f;         // J1: once raised, held until the drop is below this (hysteresis); keep it above 0 and below cn0_drop_db
  float cn0_drop_strong_db = 12.0f;  // J1: a drop this large stands alone; a smaller one needs a second indicator to start an event
  float sat_loss = 2.0f;             // J1 second indicator: tracked satellites lost against the baseline
  float jam_ind_rise = 10.0f;        // J1 second indicator: the receiver's jamming indicator above its baseline
  uint8_t min_sv_match = 3;          // satellites seen now and in the baseline needed to judge each one against its own history
  uint32_t min_event_ms = 30000;     // an event is held at least this long: a signal hovering at the threshold must not flap
  uint8_t min_sats_uniform = 8;      // S2
  uint8_t uniform_spread_x10 = 8;    // S2: spread at or below 0.8 dB
  uint8_t uniform_epochs = 10;       // S2
  uint8_t spoof_flag_epochs = 2;     // S3
  uint16_t clock_jump_ms = 2000;     // S4 (GNSS time has 1 s resolution)
  uint8_t clock_hold_epochs = 10;    // S4, S5: a jump is a one-epoch observation; the event is held
  float max_speed_mps = 80.0f;       // S5: faster than this between two fixes is not physical
  float min_jump_m = 100.0f;         // S5
  int32_t stationary_speed_mm_s = 3000;  // S5: moving this fast while the accelerometer is still
  uint8_t motion_mismatch_epochs = 5;    // S5
  uint8_t warmup_epochs = 10;        // clean epochs before a baseline exists
  uint8_t startup_holdoff_epochs = 30;  // after boot, receiver flags and time steps are not judged: a cold receiver warns and steps
  uint8_t min_sats_baseline = 4;
  uint8_t clear_epochs = 10;         // clean epochs before an event ends
  uint8_t blank_guard_epochs = 0;    // reserved: the caller decides `blanked`
};

struct Verdict {
  uint8_t event_class = kNormal;
  uint8_t confidence = 0;  // 0..31
  uint8_t rules = 0;       // Rule bits accumulated over the current event
  bool blanked = false;    // this epoch was not examined
  bool changed = false;    // event_class differs from the previous verdict
  uint8_t cn0_drop_db = 0;
  uint16_t duration_s = 0;
};

class Detector {
 public:
  explicit Detector(const DetectConfig& cfg) : cfg_(cfg) {}
  Verdict update(const Epoch& e);
  bool has_baseline() const { return baseline_ready_; }
  // A drop past the trigger that no second indicator backs (the node stays quiet: a satellite changing, not jamming).
  bool suspect() const { return suspect_; }
  float baseline() const { return baseline_; }

 private:
  void count_rules(const Epoch& e, uint8_t& now, float& drop);
  void update_baseline(const Epoch& e, float drop, bool any_rule);

  float drop_of(const Epoch& e) const;
#ifndef STZ_NO_PER_SV
  void update_sv_baseline(const Epoch& e);
  bool corroborated(const Epoch& e) const;
#endif

  DetectConfig cfg_;
  uint16_t epochs_seen_ = 0;
  bool suspect_ = false;
#ifndef STZ_NO_PER_SV
  // Each tracked satellite against its own history, plus the usual number tracked and the jamming indicator.
  uint8_t svb_n_ = 0;
  uint16_t svb_key_[ubx::kMaxSv] = {};
  float svb_[ubx::kMaxSv] = {};
  uint16_t svb_age_[ubx::kMaxSv] = {};
  bool aux_ready_ = false;
  float tracked_base_ = 0.0f, jam_ind_base_ = 0.0f;
#endif
  // baseline
  bool baseline_ready_ = false;
  float baseline_ = 0.0f;
  uint8_t warm_n_ = 0;
  float warm_sum_ = 0.0f;
  // consecutive-epoch counters
  uint8_t jam_n_ = 0, flag_n_ = 0, pos_n_ = 0, uni_n_ = 0, motion_n_ = 0;
  uint8_t clock_hold_ = 0, jump_hold_ = 0;
  // continuity
  bool have_last_ = false;
  uint32_t last_utc_ = 0, last_mono_ = 0;
  bool have_last_pos_ = false;
  int32_t last_lat_ = 0, last_lon_ = 0;
  uint32_t last_pos_mono_ = 0;
  // event
  uint8_t clean_n_ = 0;
  uint8_t mask_ = 0;
  uint8_t cls_ = kNormal;
  uint32_t onset_mono_ = 0;
  uint8_t last_confidence_ = 0;
  uint8_t last_drop_ = 0;
};

// Distance in metres between two nearby points (flat-earth approximation; fine for the tens of metres to
// few kilometres the rules compare).
float distance_m(int32_t lat1_e7, int32_t lon1_e7, int32_t lat2_e7, int32_t lon2_e7);

// The class a set of rule bits stands for. Priority: tamper, spoofing, jamming, time anomaly.
uint8_t class_of(uint8_t rules);

// Fill the telemetry fields of a witness record (docs/packet.md) from an epoch and a verdict. The caller sets
// seq, key_id and extended-only fields, and decides whether to send it.
void fill_record(const Verdict& v, const Epoch& e, Record& r);

}  // namespace stz
