#include "stz_detect.h"

#include <math.h>

namespace stz {

float distance_m(int32_t lat1, int32_t lon1, int32_t lat2, int32_t lon2) {
  const float kMetresPerE7Deg = 0.011131949f;
  float dy = (float)(lat2 - lat1) * kMetresPerE7Deg;
  float dx = (float)(lon2 - lon1) * kMetresPerE7Deg * cosf((float)lat1 * 1e-7f * 0.017453293f);
  return sqrtf(dx * dx + dy * dy);
}

uint8_t class_of(uint8_t rules) {
  if (rules & kRuleT1) return kTamper;
  if (rules & (kRuleS1 | kRuleS2 | kRuleS3 | kRuleS5)) return kSpoofing;
  if (rules & kRuleJ1) return kJamming;
  if (rules & kRuleS4) return kTimeAnomaly;
  return kNormal;
}

static uint8_t popcount8(uint8_t v) {
  uint8_t n = 0;
  for (; v; v >>= 1) n += v & 1;
  return n;
}

// Updates the consecutive-epoch counters for this epoch and returns the rules that fire now.
void Detector::count_rules(const Epoch& e, uint8_t& now, float& drop_out) {
  now = 0;
  // J1: C/N0 against the baseline, or the receiver's own jamming indicator.
  float drop = 0.0f;
  if (baseline_ready_ && !e.sat_missing) {
    float mean = e.sats_tracked > 0 ? (float)e.cn0_mean : 0.0f;  // all signals gone counts as the full drop
    drop = baseline_ - mean;
    if (drop < 0) drop = 0;
  }
  drop_out = drop;
  bool j1_hit = drop >= cfg_.cn0_drop_db || e.jam_state >= 2;
  jam_n_ = j1_hit ? (uint8_t)(jam_n_ < 255 ? jam_n_ + 1 : 255) : 0;
  if (jam_n_ >= cfg_.cn0_drop_epochs || (e.jam_state >= 3)) now |= kRuleJ1;

  // S3: receiver spoofing indicator.
  flag_n_ = e.spoof_state >= 2 ? (uint8_t)(flag_n_ < 255 ? flag_n_ + 1 : 255) : 0;
  if (flag_n_ >= cfg_.spoof_flag_epochs) now |= kRuleS3;

  // S1: fixed node away from its surveyed position while the accelerometer says stationary.
  bool s1_cond = cfg_.fixed_node && e.fix_ok && e.sensor_stationary &&
                 distance_m(cfg_.surveyed_lat_e7, cfg_.surveyed_lon_e7, e.lat_e7, e.lon_e7) > cfg_.pos_dev_m;
  pos_n_ = s1_cond ? (uint8_t)(pos_n_ < 255 ? pos_n_ + 1 : 255) : 0;
  if (pos_n_ >= cfg_.pos_dev_epochs) now |= kRuleS1;

  // S2: very uniform C/N0 over many satellites is a signature of one transmitter (suspicion). A jamming
  // drop explains low spread by itself, so it is not counted then.
  bool s2_cond = !e.sat_missing && e.sats_tracked >= cfg_.min_sats_uniform && e.cn0_mean > 0 &&
                 e.cn0_spread_x10 <= cfg_.uniform_spread_x10 && drop < cfg_.cn0_drop_db;
  uni_n_ = s2_cond ? (uint8_t)(uni_n_ < 255 ? uni_n_ + 1 : 255) : 0;
  if (uni_n_ >= cfg_.uniform_epochs) now |= kRuleS2;

  // S4: GNSS time against the node's own clock.
  if (e.utc_s != 0 && have_last_ && last_utc_ != 0) {
    int64_t d_utc = ((int64_t)e.utc_s - (int64_t)last_utc_) * 1000;
    int64_t d_mono = (int64_t)(uint32_t)(e.mono_ms - last_mono_);
    int64_t diff = d_utc - d_mono;
    if (diff < 0) diff = -diff;
    if (diff > cfg_.clock_jump_ms) clock_hold_ = cfg_.clock_hold_epochs;
  }
  if (clock_hold_ > 0) {
    now |= kRuleS4;
    clock_hold_--;
  }

  // S5: a position jump no vehicle could make, or GNSS motion while the accelerometer is still.
  if (e.fix_ok) {
    if (have_last_pos_) {
      float dt_s = (float)(uint32_t)(e.mono_ms - last_pos_mono_) / 1000.0f;
      float d = distance_m(last_lat_, last_lon_, e.lat_e7, e.lon_e7);
      if (!cfg_.fixed_node && dt_s > 0 && d >= cfg_.min_jump_m && d / dt_s > cfg_.max_speed_mps)
        jump_hold_ = cfg_.clock_hold_epochs;
    }
    last_lat_ = e.lat_e7;
    last_lon_ = e.lon_e7;
    last_pos_mono_ = e.mono_ms;
    have_last_pos_ = true;
  }
  bool mismatch = e.fix_ok && e.sensor_stationary && e.speed_mm_s > cfg_.stationary_speed_mm_s;
  motion_n_ = mismatch ? (uint8_t)(motion_n_ < 255 ? motion_n_ + 1 : 255) : 0;
  if (motion_n_ >= cfg_.motion_mismatch_epochs) now |= kRuleS5;
  if (jump_hold_ > 0) {
    now |= kRuleS5;
    jump_hold_--;
  }

  // T1: tamper sensor.
  if (e.sensor_tamper) now |= kRuleT1;

  if (e.utc_s != 0) {
    last_utc_ = e.utc_s;
    last_mono_ = e.mono_ms;
    have_last_ = true;
  }
}

void Detector::update_baseline(const Epoch& e, float drop, bool any_rule) {
  // The baseline follows only clean, healthy epochs, and never drifts down with a developing attack.
  if (any_rule || e.sat_missing || e.sats_tracked < cfg_.min_sats_baseline || !e.fix_ok) return;
  float mean = (float)e.cn0_mean;
  if (!baseline_ready_) {
    warm_sum_ += mean;
    if (++warm_n_ >= cfg_.warmup_epochs) {
      baseline_ = warm_sum_ / warm_n_;
      baseline_ready_ = true;
    }
    return;
  }
  if (drop > 1.5f) return;
  baseline_ += (mean - baseline_) * (1.0f / 32.0f);
}

Verdict Detector::update(const Epoch& e) {
  Verdict v;
  if (e.blanked) {  // our own transmitter is on: the receiver's view is not evidence, so do not touch any state
    v.blanked = true;
    v.event_class = cls_;
    v.rules = mask_;
    v.confidence = last_confidence_;
    v.cn0_drop_db = last_drop_;
    v.duration_s = cls_ == kNormal ? 0 : (uint16_t)((uint32_t)(e.mono_ms - onset_mono_) / 1000);
    return v;
  }
  uint8_t now;
  float drop;
  count_rules(e, now, drop);
  update_baseline(e, drop, now != 0 || mask_ != 0);

  uint8_t prev_cls = cls_;
  if (now != 0) {
    if (mask_ == 0) onset_mono_ = e.mono_ms;
    mask_ |= now;
    clean_n_ = 0;
  } else if (mask_ != 0 && ++clean_n_ >= cfg_.clear_epochs) {
    mask_ = 0;
    clean_n_ = 0;
  }
  cls_ = class_of(mask_);

  v.event_class = cls_;
  v.rules = mask_;
  v.changed = cls_ != prev_cls;
  uint32_t dur = cls_ == kNormal ? 0 : (uint32_t)(e.mono_ms - onset_mono_) / 1000;
  v.duration_s = dur > 65535 ? 65535 : (uint16_t)dur;
  float d = drop + 0.5f;
  v.cn0_drop_db = d >= 255.0f ? 255 : (uint8_t)d;
  if (cls_ != kNormal) {
    // Confidence: a base for any rule, more when the receiver's own flags agree, for each further rule, and
    // for how long the condition has lasted. S2 alone is only a suspicion.
    int c = (mask_ == kRuleS2) ? 8 : 12;
    if (e.jam_state >= 2 || e.spoof_state >= 2) c += 6;
    c += 4 * (popcount8(mask_) - 1);
    c += v.duration_s / 10 > 5 ? 5 : v.duration_s / 10;
    v.confidence = (uint8_t)(c > 31 ? 31 : c);
  }
  last_confidence_ = v.confidence;
  last_drop_ = v.cn0_drop_db;
  return v;
}

void fill_record(const Verdict& v, const Epoch& e, Record& r) {
  r.type = v.event_class == kNormal ? kHeartbeat : kEvent;
  r.event_class = v.event_class;
  r.confidence = v.confidence;
  r.lat_e7 = e.lat_e7;
  r.lon_e7 = e.lon_e7;
  r.timestamp = e.utc_s;
  r.duration_s = v.duration_s;
  r.cn0_mean = e.cn0_mean;
  r.cn0_drop = v.cn0_drop_db;
  r.cn0_spread = (uint8_t)((e.cn0_spread_x10 + 5) / 10);
  r.agc = e.jam_ind;
  r.rx_jamming = e.jam_state >= 2;
  r.rx_spoofing = e.spoof_state >= 2;
  r.stationary = e.sensor_stationary;
  r.tamper = (v.rules & kRuleT1) != 0;
  r.extended = false;
}

}  // namespace stz
