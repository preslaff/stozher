// Detection rules on recorded epochs. The recordings are SYNTHETIC (a fixed pseudo-random generator, a
// healthy fixed receiver, then scripted attacks); they stand in for real captures until we have some.
#include <string.h>
#include <unity.h>

#include "stz_codec.h"
#include "stz_detect.h"

using namespace stz;

static const int32_t kLat = 425000000, kLon = 270000000;
static const uint32_t kUtc0 = 1795775405u;

// Deterministic noise in [-1, 1].
struct Rng {
  uint32_t s = 12345;
  float next() {
    s = s * 1664525u + 1013904223u;
    return ((s >> 8) & 0xFFFF) / 32767.5f - 1.0f;
  }
};

struct Recorder {
  Rng rng;
  Detector det;
  uint32_t t = 0;  // seconds since the start
  Epoch tmpl;      // what the next epoch looks like; tests edit it between runs
  int changes = 0;
  Verdict last;
  explicit Recorder(const DetectConfig& cfg) : det(cfg) {
    tmpl.fix_ok = true;
    tmpl.num_sv = 14;
    tmpl.lat_e7 = kLat;
    tmpl.lon_e7 = kLon;
    tmpl.h_acc_mm = 1500;
    tmpl.sats_tracked = 12;
    tmpl.sats_used = 11;
    tmpl.cn0_mean = 42;
    tmpl.cn0_spread_x10 = 38;
    tmpl.jam_state = 1;
    tmpl.spoof_state = 1;
    tmpl.sensor_stationary = true;
  }
  // Runs `n` seconds with the template (plus noise on C/N0); returns the first second at which the class was
  // not Normal, or -1.
  int run(int n, int noise = 1) {
    int first = -1;
    for (int i = 0; i < n; i++, t++) {
      Epoch e = tmpl;
      e.utc_s = kUtc0 + t + (utc_offset);
      e.mono_ms = 100000u + t * 1000u;
      if (noise) e.cn0_mean = (uint8_t)(e.cn0_mean + (rng.next() > 0.3f ? 1 : 0) - (rng.next() < -0.3f ? 1 : 0));
      last = det.update(e);
      if (last.changed) changes++;
      if (first < 0 && last.event_class != kNormal) first = i;
    }
    return first;
  }
  int32_t utc_offset = 0;
};

static DetectConfig fixed_cfg() {
  DetectConfig c;
  c.fixed_node = true;
  c.surveyed_lat_e7 = kLat;
  c.surveyed_lon_e7 = kLon;
  return c;
}

void test_ten_minutes_of_normal_operation_raise_nothing() {
  Recorder r(fixed_cfg());
  TEST_ASSERT_EQUAL(-1, r.run(600));
  TEST_ASSERT_EQUAL(0, r.changes);
  TEST_ASSERT_TRUE(r.det.has_baseline());
  TEST_ASSERT_FLOAT_WITHIN(1.0f, 42.0f, r.det.baseline());
}

void test_a_cn0_drop_is_jamming_within_three_epochs_and_clears() {
  Recorder r(fixed_cfg());
  r.run(60);
  r.tmpl.cn0_mean = 28;  // 14 dB below the baseline
  r.tmpl.sats_tracked = 6;
  r.tmpl.jam_ind = 40;  // and the receiver's noise indicator is up: that is what makes it jamming
  int first = r.run(30);
  TEST_ASSERT_EQUAL(2, first);  // the third epoch of the drop
  TEST_ASSERT_EQUAL(kJamming, r.last.event_class);
  TEST_ASSERT_TRUE(r.last.rules & kRuleJ1);
  TEST_ASSERT_TRUE(r.last.cn0_drop_db >= 12);
  TEST_ASSERT_TRUE(r.last.duration_s >= 25);
  r.tmpl.cn0_mean = 42;
  r.tmpl.sats_tracked = 12;
  r.tmpl.jam_ind = 0;
  r.run(9);
  TEST_ASSERT_EQUAL(kJamming, r.last.event_class);  // still held: ten clean epochs end it
  r.run(3);
  TEST_ASSERT_EQUAL(kNormal, r.last.event_class);
  TEST_ASSERT_EQUAL(2, r.changes);  // onset and end, nothing else
}

// A signal that hovers around the trigger level (a partial cover, a marginal site) must give one event, not a
// stream of start/stop records: every status change costs a satellite message.
void test_a_signal_hovering_at_the_threshold_gives_one_event() {
  Recorder r(fixed_cfg());
  r.run(60, 0);
  r.tmpl.jam_ind = 40;
  for (int k = 0; k < 8; k++) {
    r.tmpl.cn0_mean = 34;  // 8 dB below the baseline, and satellites lost: raises it
    r.tmpl.sats_tracked = 9;
    r.run(20, 0);
    r.tmpl.cn0_mean = 37;  // 5 dB below: under the trigger level, over the clear level
    r.tmpl.sats_tracked = 12;
    r.run(20, 0);
  }
  TEST_ASSERT_EQUAL(kJamming, r.last.event_class);
  TEST_ASSERT_EQUAL(1, r.changes);
  r.tmpl.cn0_mean = 42;  // the signal is really back
  r.tmpl.jam_ind = 0;
  r.run(12, 0);
  TEST_ASSERT_EQUAL(kNormal, r.last.event_class);
  TEST_ASSERT_EQUAL(2, r.changes);
}

void test_an_event_is_held_while_the_drop_is_above_the_clear_level() {
  Recorder r(fixed_cfg());
  r.run(60, 0);
  r.tmpl.cn0_mean = 30;
  r.tmpl.jam_ind = 40;
  r.run(10, 0);
  TEST_ASSERT_EQUAL(kJamming, r.last.event_class);
  r.tmpl.cn0_mean = 38;  // 4 dB below the baseline: better, but not clear
  r.run(120, 0);
  TEST_ASSERT_EQUAL(kJamming, r.last.event_class);
  r.tmpl.cn0_mean = 40;  // 2 dB below: clear
  r.run(12, 0);
  TEST_ASSERT_EQUAL(kNormal, r.last.event_class);
}

void test_a_short_event_is_held_for_its_minimum_length() {
  Recorder r(fixed_cfg());
  r.run(60, 0);
  r.tmpl.cn0_mean = 28;
  r.tmpl.jam_ind = 40;
  r.run(5, 0);  // raised on the third epoch
  TEST_ASSERT_EQUAL(kJamming, r.last.event_class);
  r.tmpl.cn0_mean = 42;
  r.tmpl.jam_ind = 0;
  r.run(12, 0);  // ten clean epochs have passed, but the event is only about 14 s old
  TEST_ASSERT_EQUAL(kJamming, r.last.event_class);
  r.run(20, 0);
  TEST_ASSERT_EQUAL(kNormal, r.last.event_class);
  TEST_ASSERT_EQUAL(2, r.changes);
}

void test_a_drop_does_not_move_the_baseline() {
  Recorder r(fixed_cfg());
  r.run(60);
  float before = r.det.baseline();
  r.tmpl.cn0_mean = 25;
  r.run(120);
  TEST_ASSERT_EQUAL_FLOAT(before, r.det.baseline());
}

void test_total_loss_of_signals_with_the_noise_up_is_jamming() {
  Recorder r(fixed_cfg());
  r.run(60);
  r.tmpl.jam_ind = 40;
  r.tmpl.sats_tracked = 0;
  r.tmpl.cn0_mean = 0;
  r.tmpl.cn0_spread_x10 = 0;
  r.tmpl.fix_ok = false;
  r.run(10, 0);
  TEST_ASSERT_EQUAL(kJamming, r.last.event_class);
}

void test_a_slow_decline_is_not_absorbed_into_the_baseline() {
  Recorder r(fixed_cfg());
  r.run(60);
  int detected = -1;
  for (int i = 0; i < 300 && detected < 0; i++) {
    r.tmpl.cn0_mean = (uint8_t)(42 - i / 10);  // 0.1 dB per second
    r.run(1, 0);
    if (r.last.event_class != kNormal) detected = i;  // loss of reception here: the noise indicator stays flat
  }
  TEST_ASSERT_TRUE(detected > 0);
  TEST_ASSERT_TRUE(detected < 150);  // 6 dB down at 60 s, then the 60 s a loss must last  // the baseline did not follow the signal down
}

void test_the_receiver_jamming_flag_alone_is_jamming_once_it_lasts() {
  Recorder r(fixed_cfg());
  r.run(30);
  r.tmpl.jam_state = 2;
  TEST_ASSERT_EQUAL(-1, r.run(100));  // healthy signals, flat noise: not yet
  int first = r.run(40);
  TEST_ASSERT_EQUAL(19, first);       // 120 epochs of the flag alone
  TEST_ASSERT_EQUAL(kJamming, r.last.event_class);
  TEST_ASSERT_TRUE(r.last.confidence >= 18);  // the receiver agrees: higher confidence
}

// a4b0 indoors, 28 Oct 2026: the NEO-6M said warning and critical for under a minute while the satellites were weak but
// steady (drop 1-3 dB, jamming indicator 3, noise flat). Twice it raised a jamming event and a hotspot.
void test_a_short_receiver_flag_with_steady_weak_signals_is_not_jamming() {
  Recorder r(fixed_cfg());
  r.tmpl.cn0_mean = 20;
  r.tmpl.jam_ind = 3;
  r.run(120, 0);
  r.tmpl.cn0_mean = 18;
  r.tmpl.jam_state = 3;
  r.run(20, 0);
  r.tmpl.jam_state = 2;
  r.run(30, 0);
  r.tmpl.jam_state = 1;
  r.run(30, 0);
  TEST_ASSERT_EQUAL(0, r.changes);
}

void test_a_critical_receiver_flag_with_a_real_drop_is_jamming_at_once() {
  Recorder r(fixed_cfg());
  r.run(60, 0);
  r.tmpl.cn0_mean = 33;
  r.tmpl.jam_state = 3;
  TEST_ASSERT_EQUAL(0, r.run(5, 0));
  TEST_ASSERT_EQUAL(kJamming, r.last.event_class);
}

// a4b0 indoors, 27-28 Oct 2026: with no satellites at all the loss of reception was raised, then cleared after 30 s,
// then raised again a minute later, all day. One epoch of the receiver's warning restarted the loss count.
void test_a_flickering_receiver_warning_does_not_end_a_loss_of_reception() {
  Recorder r(fixed_cfg());
  r.run(120, 0);
  r.tmpl.sats_tracked = 0;
  r.tmpl.cn0_mean = 0;
  r.tmpl.fix_ok = false;
  for (int i = 0; i < 20; i++) {
    r.run(89, 0);
    r.tmpl.jam_state = 2;
    r.run(1, 0);
    r.tmpl.jam_state = 1;
  }
  TEST_ASSERT_EQUAL(kBenign, r.last.event_class);
  TEST_ASSERT_EQUAL(1, r.changes);  // raised once, never flipped back
}

// Noise that lasts during a loss is still jamming.
void test_noise_that_lasts_during_a_loss_of_reception_is_jamming() {
  Recorder r(fixed_cfg());
  r.run(120, 0);
  r.tmpl.cn0_mean = 24;
  r.run(120, 0);
  TEST_ASSERT_EQUAL(kBenign, r.last.event_class);
  r.tmpl.jam_ind = 40;
  r.run(5, 0);
  TEST_ASSERT_EQUAL(kJamming, r.last.event_class);
}

void test_no_cn0_rule_fires_before_a_baseline_exists() {
  Recorder r(fixed_cfg());
  r.tmpl.cn0_mean = 20;  // a poor site, from the first second
  TEST_ASSERT_EQUAL(-1, r.run(8));
  TEST_ASSERT_FALSE(r.det.has_baseline());
}

void test_the_receiver_spoofing_flag_is_spoofing_after_two_epochs() {
  Recorder r(fixed_cfg());
  r.run(30);
  r.tmpl.spoof_state = 2;
  int first = r.run(10);
  TEST_ASSERT_EQUAL(1, first);
  TEST_ASSERT_EQUAL(kSpoofing, r.last.event_class);
  TEST_ASSERT_TRUE(r.last.rules & kRuleS3);
  r.tmpl.spoof_state = 3;
  r.run(2);
  TEST_ASSERT_EQUAL(kSpoofing, r.last.event_class);
}

void test_position_drift_of_a_stationary_fixed_node_is_spoofing() {
  Recorder r(fixed_cfg());
  r.run(30);
  r.tmpl.lat_e7 = kLat + 20000;  // about 220 m north
  int first = r.run(10);
  TEST_ASSERT_EQUAL(4, first);  // five epochs of deviation
  TEST_ASSERT_EQUAL(kSpoofing, r.last.event_class);
  TEST_ASSERT_TRUE(r.last.rules & kRuleS1);
}

void test_position_drift_is_ignored_when_the_node_is_being_moved() {
  Recorder r(fixed_cfg());
  r.run(30);
  r.tmpl.sensor_stationary = false;
  r.tmpl.sensor_moving = true;
  r.tmpl.lat_e7 = kLat + 20000;
  TEST_ASSERT_EQUAL(-1, r.run(30));
}

void test_uniform_cn0_over_many_satellites_is_a_suspicion() {
  Recorder r(fixed_cfg());
  r.run(30);
  r.tmpl.cn0_spread_x10 = 4;  // 0.4 dB across 12 satellites
  int first = r.run(20);
  TEST_ASSERT_EQUAL(9, first);  // ten epochs
  TEST_ASSERT_EQUAL(kSpoofing, r.last.event_class);
  TEST_ASSERT_EQUAL(kRuleS2, r.last.rules);
  TEST_ASSERT_TRUE(r.last.confidence <= 14);  // a suspicion, not a certainty
}

void test_uniform_cn0_over_few_satellites_is_not() {
  Recorder r(fixed_cfg());
  r.run(30);
  r.tmpl.cn0_spread_x10 = 4;
  r.tmpl.sats_tracked = 5;
  TEST_ASSERT_EQUAL(-1, r.run(30));
}

void test_a_clock_jump_is_a_time_anomaly_held_then_cleared() {
  Recorder r(fixed_cfg());
  r.run(30);
  r.utc_offset = 3600;
  int first = r.run(1);
  TEST_ASSERT_EQUAL(0, first);
  TEST_ASSERT_EQUAL(kTimeAnomaly, r.last.event_class);
  r.run(35);  // GNSS time is consistent again, one hour on; every event is held at least min_event_s (30 s)
  TEST_ASSERT_EQUAL(kNormal, r.last.event_class);
  TEST_ASSERT_EQUAL(2, r.changes);
}

void test_a_mobile_node_is_not_flagged_for_driving() {
  DetectConfig c;  // not a fixed node
  Recorder r(c);
  r.tmpl.sensor_stationary = false;
  r.tmpl.sensor_moving = true;
  r.tmpl.speed_mm_s = 20000;
  for (int i = 0; i < 120; i++) {
    r.tmpl.lat_e7 = kLat + i * 180;  // 20 m per second northwards
    r.run(1);
  }
  TEST_ASSERT_EQUAL(0, r.changes);
}

void test_an_impossible_position_jump_of_a_mobile_node_is_spoofing() {
  DetectConfig c;
  Recorder r(c);
  r.tmpl.sensor_stationary = false;
  r.run(30);
  r.tmpl.lat_e7 = kLat + 450000;  // 5 km in one second
  int first = r.run(1);
  TEST_ASSERT_EQUAL(0, first);
  TEST_ASSERT_EQUAL(kSpoofing, r.last.event_class);
  TEST_ASSERT_TRUE(r.last.rules & kRuleS5);
}

void test_gnss_speed_with_a_still_accelerometer_is_spoofing() {
  DetectConfig c;
  Recorder r(c);
  r.run(30);
  r.tmpl.speed_mm_s = 15000;
  int first = r.run(10);
  TEST_ASSERT_EQUAL(4, first);
  TEST_ASSERT_EQUAL(kSpoofing, r.last.event_class);
}

void test_tamper_is_reported_and_outranks_the_rest() {
  Recorder r(fixed_cfg());
  r.run(30);
  r.tmpl.sensor_tamper = true;
  TEST_ASSERT_EQUAL(0, r.run(1));
  TEST_ASSERT_EQUAL(kTamper, r.last.event_class);
  r.tmpl.spoof_state = 2;
  r.run(5);
  TEST_ASSERT_EQUAL(kTamper, r.last.event_class);
  TEST_ASSERT_TRUE(r.last.rules & kRuleS3);  // the other evidence is still carried
}

void test_blanked_epochs_are_not_evidence_and_change_nothing() {
  Recorder r(fixed_cfg());
  r.run(60);
  float before = r.det.baseline();
  r.tmpl.blanked = true;
  r.tmpl.cn0_mean = 15;  // our own KIM1 transmission wrecks the receiver
  r.tmpl.jam_state = 3;
  r.tmpl.spoof_state = 3;
  TEST_ASSERT_EQUAL(-1, r.run(30));
  TEST_ASSERT_TRUE(r.last.blanked);
  TEST_ASSERT_EQUAL(kNormal, r.last.event_class);
  TEST_ASSERT_EQUAL_FLOAT(before, r.det.baseline());
  r.tmpl.blanked = false;
  r.tmpl.cn0_mean = 42;
  r.tmpl.jam_state = 1;
  r.tmpl.spoof_state = 1;
  TEST_ASSERT_EQUAL(-1, r.run(20));  // and no leftover counters fire afterwards
  TEST_ASSERT_EQUAL(0, r.changes);
}

void test_a_real_attack_during_blanking_is_seen_once_blanking_ends() {
  Recorder r(fixed_cfg());
  r.run(60);
  r.tmpl.blanked = true;
  r.tmpl.cn0_mean = 20;
  r.tmpl.jam_ind = 40;
  r.run(10);
  r.tmpl.blanked = false;  // the attack is still there
  int first = r.run(10);
  TEST_ASSERT_EQUAL(2, first);
  TEST_ASSERT_EQUAL(kJamming, r.last.event_class);
}

void test_blanked_epochs_inside_an_event_keep_the_event() {
  Recorder r(fixed_cfg());
  r.run(60);
  r.tmpl.cn0_mean = 20;
  r.tmpl.jam_ind = 40;
  r.run(10);
  TEST_ASSERT_EQUAL(kJamming, r.last.event_class);
  r.tmpl.blanked = true;
  r.run(5);
  TEST_ASSERT_TRUE(r.last.blanked);
  TEST_ASSERT_EQUAL(kJamming, r.last.event_class);
  TEST_ASSERT_FALSE(r.last.changed);
}

// --- J1 per satellite, and corroboration ---------------------------------------------------------------------

// `n` satellites with keys first_key.. all at `cn0`.
static void set_svs(Epoch& e, uint8_t n, uint16_t first_key, uint8_t cn0, uint8_t at = 0) {
  for (uint8_t i = 0; i < n; i++) {
    e.sv_key[at + i] = (uint16_t)(first_key + i);
    e.sv_cno[at + i] = cn0;
  }
  e.sv_n = (uint8_t)(at + n);
}

// Six strong satellites set and six weak low ones rise: the mean C/N0 falls 7 dB with no jamming at all.
void test_a_change_of_the_tracked_satellites_is_not_a_drop() {
  Recorder r(fixed_cfg());
  set_svs(r.tmpl, 6, 1, 45);
  set_svs(r.tmpl, 6, 20, 39, 6);
  r.tmpl.cn0_mean = 42;
  r.run(120, 0);
  TEST_ASSERT_EQUAL(0, r.changes);
  set_svs(r.tmpl, 6, 20, 39);  // the strong ones are gone ...
  set_svs(r.tmpl, 6, 40, 30, 6);  // ... new ones come in low
  r.tmpl.cn0_mean = 34;  // the mean is 8 dB down
  r.run(120, 0);
  TEST_ASSERT_EQUAL(0, r.changes);
  TEST_ASSERT_EQUAL(kNormal, r.last.event_class);
}

// Every satellite falls together: that is jamming, whatever the mean says.
void test_every_satellite_falling_together_is_jamming() {
  Recorder r(fixed_cfg());
  set_svs(r.tmpl, 12, 1, 42);
  r.run(120, 0);
  set_svs(r.tmpl, 12, 1, 22);
  r.tmpl.cn0_mean = 22;
  r.tmpl.jam_ind = 40;
  r.run(10, 0);
  TEST_ASSERT_EQUAL(kJamming, r.last.event_class);
}

// A drop without any sign of noise is loss of reception: nothing for the first minute (the node only knows it is
// suspicious), then one class 3 (benign degradation) event, never jamming.
void test_a_drop_without_noise_is_loss_of_reception_after_a_minute() {
  Recorder r(fixed_cfg());
  r.run(60, 0);
  r.tmpl.cn0_mean = 33;  // 9 dB down; the same satellites, the noise indicator flat
  r.run(50, 0);
  TEST_ASSERT_EQUAL(kNormal, r.last.event_class);
  TEST_ASSERT_EQUAL(0, r.changes);
  TEST_ASSERT_TRUE(r.det.suspect());
  r.run(15, 0);
  TEST_ASSERT_EQUAL(kBenign, r.last.event_class);
  TEST_ASSERT_TRUE(r.last.rules & kRuleL1);
  TEST_ASSERT_FALSE(r.last.rules & kRuleJ1);
  TEST_ASSERT_TRUE(r.last.confidence >= 6 && r.last.confidence <= 10);
  TEST_ASSERT_EQUAL(1, r.changes);
  r.tmpl.cn0_mean = 42;
  r.run(40, 0);
  TEST_ASSERT_EQUAL(kNormal, r.last.event_class);
  TEST_ASSERT_EQUAL(2, r.changes);
}

// A dip shorter than a minute (an underpass) sends nothing.
void test_a_short_dip_without_noise_sends_nothing() {
  Recorder r(fixed_cfg());
  r.run(60, 0);
  r.tmpl.cn0_mean = 25;
  r.run(40, 0);
  r.tmpl.cn0_mean = 42;
  r.run(60, 0);
  TEST_ASSERT_EQUAL(0, r.changes);
  TEST_ASSERT_EQUAL(kNormal, r.last.event_class);
}

// The noise indicator comes up while a loss is already reported: the class rises to jamming, once.
void test_a_loss_becomes_jamming_when_the_noise_rises() {
  Recorder r(fixed_cfg());
  r.run(60, 0);
  r.tmpl.cn0_mean = 28;
  r.run(70, 0);
  TEST_ASSERT_EQUAL(kBenign, r.last.event_class);
  r.tmpl.jam_ind = 40;
  r.run(10, 0);
  TEST_ASSERT_EQUAL(kJamming, r.last.event_class);
  TEST_ASSERT_EQUAL(2, r.changes);
}

// The receiver's noise level on its own is noise evidence too.
void test_a_rise_of_the_noise_level_is_jamming() {
  Recorder r(fixed_cfg());
  r.run(60, 0);
  r.tmpl.cn0_mean = 30;
  r.tmpl.noise = 120;  // baseline 0 in this template: well above noise_rise
  r.run(10, 0);
  TEST_ASSERT_EQUAL(kJamming, r.last.event_class);
}

void test_a_modest_drop_with_the_noise_up_is_jamming() {
  Recorder r(fixed_cfg());
  r.run(60, 0);
  r.tmpl.cn0_mean = 33;
  r.tmpl.jam_ind = 40;
  r.run(10, 0);
  TEST_ASSERT_EQUAL(kJamming, r.last.event_class);
}

void test_a_modest_drop_with_the_receiver_warning_is_jamming() {
  Recorder r(fixed_cfg());
  r.run(60, 0);
  r.tmpl.cn0_mean = 33;
  r.tmpl.jam_state = 2;
  r.run(10, 0);
  TEST_ASSERT_EQUAL(kJamming, r.last.event_class);
}

// Even a very large drop is not jamming without noise evidence: the platform's own rule (docs/scenario.md).
void test_a_large_drop_without_noise_is_still_loss_of_reception() {
  Recorder r(fixed_cfg());
  r.run(60, 0);
  r.tmpl.cn0_mean = 24;  // 18 dB
  r.run(120, 0);
  TEST_ASSERT_EQUAL(kBenign, r.last.event_class);
}

// The cover test on the T-Beam: a strong drop with satellites still tracked but weak, then the signal comes back.
// Per-satellite history must not keep the event alive once every satellite is back where it was.
void test_a_covered_receiver_is_loss_of_reception_and_clears_when_the_satellites_return() {
  Recorder r(fixed_cfg());
  set_svs(r.tmpl, 8, 1, 26);
  r.tmpl.sats_tracked = 8;
  r.tmpl.cn0_mean = 26;
  r.run(120, 0);
  set_svs(r.tmpl, 8, 1, 13);  // the cover: 13 dB down, all of them, the noise indicator flat
  r.tmpl.cn0_mean = 13;
  r.run(90, 0);
  TEST_ASSERT_EQUAL(kBenign, r.last.event_class);
  r.run(1500, 0);  // long cover: the baseline must not follow it
  TEST_ASSERT_EQUAL(kBenign, r.last.event_class);
  set_svs(r.tmpl, 8, 1, 26);  // uncovered
  r.tmpl.cn0_mean = 26;
  r.run(60, 0);
  TEST_ASSERT_EQUAL(kNormal, r.last.event_class);
}

// The same, but the satellites come back one by one, with a different few first (a receiver reacquiring).
void test_an_event_clears_while_the_receiver_reacquires_satellites() {
  Recorder r(fixed_cfg());
  set_svs(r.tmpl, 8, 1, 26);
  r.tmpl.sats_tracked = 8;
  r.tmpl.cn0_mean = 26;
  r.run(120, 0);
  r.tmpl.sats_tracked = 0;  // everything lost under the cover
  r.tmpl.sv_n = 0;
  r.tmpl.cn0_mean = 0;
  r.run(600, 0);
  TEST_ASSERT_EQUAL(kBenign, r.last.event_class);
  for (uint8_t n = 1; n <= 8; n++) {  // one more satellite every 20 s, new ids first
    set_svs(r.tmpl, n, (uint16_t)(100 + n), 24);
    r.tmpl.sats_tracked = n;
    r.tmpl.cn0_mean = 24;
    r.run(20, 0);
  }
  r.run(60, 0);
  TEST_ASSERT_EQUAL(kNormal, r.last.event_class);
}

void test_the_same_recording_always_gives_the_same_verdicts() {
  Verdict a[200], b[200];
  for (int pass = 0; pass < 2; pass++) {
    Recorder r(fixed_cfg());
    r.run(80);
    r.tmpl.cn0_mean = 30;
    for (int i = 0; i < 120; i++) {
      r.run(1);
      (pass == 0 ? a : b)[i] = r.last;
    }
  }
  TEST_ASSERT_EQUAL_MEMORY(a, b, sizeof(Verdict) * 120);
}

void test_a_verdict_becomes_a_record_the_codec_round_trips() {
  Recorder r(fixed_cfg());
  r.run(60);
  r.tmpl.cn0_mean = 28;
  r.tmpl.sats_tracked = 6;
  r.tmpl.jam_state = 2;
  r.tmpl.utc_s = 0;
  r.run(5);
  Epoch e = r.tmpl;
  e.utc_s = kUtc0 + 65;
  Record rec;
  rec.seq = 7;
  rec.key_id = 0;
  fill_record(r.last, e, rec);
  TEST_ASSERT_EQUAL(kEvent, rec.type);
  TEST_ASSERT_EQUAL(kJamming, rec.event_class);
  TEST_ASSERT_TRUE(rec.rx_jamming);
  TEST_ASSERT_EQUAL_UINT8(28, rec.cn0_mean);

  uint8_t master[16];
  for (int i = 0; i < 16; i++) master[i] = (uint8_t)i;  // the public test key
  Keys keys;
  derive_keys(master, 1, 0, keys);
  uint8_t raw[kMaxLen];
  size_t n = encode(rec, 1, keys, raw);
  Record back;
  TEST_ASSERT_EQUAL(Status::Ok, decode(raw, n, 1, master, back));
  TEST_ASSERT_EQUAL(rec.event_class, back.event_class);
  TEST_ASSERT_EQUAL(rec.confidence, back.confidence);
  TEST_ASSERT_EQUAL_UINT8(rec.cn0_drop, back.cn0_drop);
  TEST_ASSERT_EQUAL_INT32(kLat, back.lat_e7);
}

void test_a_cold_receiver_may_warn_and_step_its_clock_without_raising_anything() {
  Recorder r(fixed_cfg());
  r.tmpl.jam_state = 2;   // warning while it acquires
  r.tmpl.spoof_state = 2;
  r.tmpl.fix_ok = false;
  r.run(10);
  r.tmpl.fix_ok = true;
  r.utc_offset = 7200;    // the first fix corrects the time by two hours
  TEST_ASSERT_EQUAL(-1, r.run(15));
  TEST_ASSERT_EQUAL(0, r.changes);
}

void test_a_flag_that_persists_past_the_holdoff_is_still_reported() {
  Recorder r(fixed_cfg());
  r.tmpl.jam_state = 2;  // booted into real jamming: no baseline to compare with, so the flag must last
  int first = r.run(200);
  TEST_ASSERT_TRUE(first >= 30 + 118);  // not during acquisition, then two minutes of the flag alone
  TEST_ASSERT_TRUE(first <= 30 + 122);
  TEST_ASSERT_EQUAL(kJamming, r.last.event_class);
}

void test_time_without_a_fix_is_not_a_reference_for_the_clock_rule() {
  Recorder r(fixed_cfg());
  r.run(40);
  r.tmpl.fix_ok = false;   // fix lost
  r.utc_offset = 5000;     // the receiver's RTC drifts or is reset while it has no fix
  r.run(5);
  r.tmpl.fix_ok = true;    // fix returns with the old, correct time
  r.utc_offset = 0;
  TEST_ASSERT_EQUAL(-1, r.run(20));
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_ten_minutes_of_normal_operation_raise_nothing);
  RUN_TEST(test_a_cn0_drop_is_jamming_within_three_epochs_and_clears);
  RUN_TEST(test_a_signal_hovering_at_the_threshold_gives_one_event);
  RUN_TEST(test_an_event_is_held_while_the_drop_is_above_the_clear_level);
  RUN_TEST(test_a_short_event_is_held_for_its_minimum_length);
  RUN_TEST(test_a_drop_does_not_move_the_baseline);
  RUN_TEST(test_total_loss_of_signals_with_the_noise_up_is_jamming);
  RUN_TEST(test_a_slow_decline_is_not_absorbed_into_the_baseline);
  RUN_TEST(test_the_receiver_jamming_flag_alone_is_jamming_once_it_lasts);
  RUN_TEST(test_a_short_receiver_flag_with_steady_weak_signals_is_not_jamming);
  RUN_TEST(test_a_critical_receiver_flag_with_a_real_drop_is_jamming_at_once);
  RUN_TEST(test_a_flickering_receiver_warning_does_not_end_a_loss_of_reception);
  RUN_TEST(test_noise_that_lasts_during_a_loss_of_reception_is_jamming);
  RUN_TEST(test_no_cn0_rule_fires_before_a_baseline_exists);
  RUN_TEST(test_the_receiver_spoofing_flag_is_spoofing_after_two_epochs);
  RUN_TEST(test_position_drift_of_a_stationary_fixed_node_is_spoofing);
  RUN_TEST(test_position_drift_is_ignored_when_the_node_is_being_moved);
  RUN_TEST(test_uniform_cn0_over_many_satellites_is_a_suspicion);
  RUN_TEST(test_uniform_cn0_over_few_satellites_is_not);
  RUN_TEST(test_a_clock_jump_is_a_time_anomaly_held_then_cleared);
  RUN_TEST(test_a_mobile_node_is_not_flagged_for_driving);
  RUN_TEST(test_an_impossible_position_jump_of_a_mobile_node_is_spoofing);
  RUN_TEST(test_gnss_speed_with_a_still_accelerometer_is_spoofing);
  RUN_TEST(test_tamper_is_reported_and_outranks_the_rest);
  RUN_TEST(test_blanked_epochs_are_not_evidence_and_change_nothing);
  RUN_TEST(test_a_real_attack_during_blanking_is_seen_once_blanking_ends);
  RUN_TEST(test_blanked_epochs_inside_an_event_keep_the_event);
  RUN_TEST(test_a_cold_receiver_may_warn_and_step_its_clock_without_raising_anything);
  RUN_TEST(test_a_flag_that_persists_past_the_holdoff_is_still_reported);
  RUN_TEST(test_time_without_a_fix_is_not_a_reference_for_the_clock_rule);
  RUN_TEST(test_a_change_of_the_tracked_satellites_is_not_a_drop);
  RUN_TEST(test_every_satellite_falling_together_is_jamming);
  RUN_TEST(test_a_drop_without_noise_is_loss_of_reception_after_a_minute);
  RUN_TEST(test_a_short_dip_without_noise_sends_nothing);
  RUN_TEST(test_a_loss_becomes_jamming_when_the_noise_rises);
  RUN_TEST(test_a_rise_of_the_noise_level_is_jamming);
  RUN_TEST(test_a_modest_drop_with_the_noise_up_is_jamming);
  RUN_TEST(test_a_modest_drop_with_the_receiver_warning_is_jamming);
  RUN_TEST(test_a_large_drop_without_noise_is_still_loss_of_reception);
  RUN_TEST(test_a_covered_receiver_is_loss_of_reception_and_clears_when_the_satellites_return);
  RUN_TEST(test_an_event_clears_while_the_receiver_reacquires_satellites);
  RUN_TEST(test_the_same_recording_always_gives_the_same_verdicts);
  RUN_TEST(test_a_verdict_becomes_a_record_the_codec_round_trips);
  return UNITY_END();
}
