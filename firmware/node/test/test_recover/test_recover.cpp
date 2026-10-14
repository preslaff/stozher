// Receiver recovery on the host: when it asks for a restart, when it must not, and what the detector does after one.
// Epochs are SYNTHETIC.
#include <unity.h>

#include "stz_node.h"
#include "stz_recover.h"

using namespace stz;

static Epoch epoch(uint32_t t_s, bool fix) {
  Epoch e;
  e.mono_ms = 100000u + t_s * 1000u;
  e.fix_ok = fix;
  e.sats_tracked = fix ? 10 : 0;
  e.cn0_mean = fix ? 40 : 0;
  return e;
}

// Seconds of no fix with a clean band, from t0; returns the second of the first restart, or -1.
static int run_no_fix(ReceiverWatch& w, uint32_t t0, uint32_t secs, bool clean = true) {
  for (uint32_t t = 0; t < secs; t++)
    if (w.update(epoch(t0 + t, false), clean) == RecoverAction::Restart) return (int)t;
  return -1;
}

void test_a_clean_band_without_a_fix_restarts_the_receiver_after_five_minutes() {
  ReceiverWatch w{RecoverConfig{}};
  TEST_ASSERT_EQUAL(-1, run_no_fix(w, 0, 299));
  TEST_ASSERT_EQUAL(0, w.restarts());
  TEST_ASSERT_EQUAL(RecoverAction::Restart, w.update(epoch(300, false), true));
  TEST_ASSERT_EQUAL(1, w.restarts());
}

void test_it_never_restarts_while_the_band_is_not_clean() {
  ReceiverWatch w{RecoverConfig{}};
  TEST_ASSERT_EQUAL(-1, run_no_fix(w, 0, 3600, false));  // real jamming for an hour: no restart
  TEST_ASSERT_EQUAL(0, w.restarts());
}

void test_it_never_restarts_with_a_fix() {
  ReceiverWatch w{RecoverConfig{}};
  for (uint32_t t = 0; t < 3600; t++) TEST_ASSERT_EQUAL(RecoverAction::None, w.update(epoch(t, true), true));
}

void test_a_fix_or_a_noisy_band_restarts_the_timer() {
  ReceiverWatch w{RecoverConfig{}};
  run_no_fix(w, 0, 250);
  TEST_ASSERT_EQUAL(RecoverAction::None, w.update(epoch(250, true), true));  // the fix returns
  TEST_ASSERT_EQUAL(-1, run_no_fix(w, 251, 250));  // a fresh five minutes are needed
  w.update(epoch(501, false), false);              // the band turns noisy
  TEST_ASSERT_EQUAL(-1, run_no_fix(w, 502, 250));
  TEST_ASSERT_EQUAL(0, w.restarts());
}

void test_restarts_are_spaced_then_slowed() {
  ReceiverWatch w{RecoverConfig{}};
  uint32_t t = 0;
  int restarts_at[8];
  int n = 0;
  for (; t < 6 * 3600 && n < 8; t++)
    if (w.update(epoch(t, false), true) == RecoverAction::Restart) restarts_at[n++] = (int)t;
  TEST_ASSERT_EQUAL(8, n);
  TEST_ASSERT_EQUAL(300, restarts_at[0]);
  TEST_ASSERT_EQUAL(900, restarts_at[1]);    // +10 min
  TEST_ASSERT_EQUAL(1500, restarts_at[2]);   // +10 min
  TEST_ASSERT_EQUAL(5100, restarts_at[3]);   // then hourly: an indoor node does not restart all day
  TEST_ASSERT_EQUAL(8700, restarts_at[4]);
}

void test_a_fix_resets_the_try_count() {
  ReceiverWatch w{RecoverConfig{}};
  for (uint32_t t = 0; t < 2000; t++) w.update(epoch(t, false), true);  // three restarts at 300, 900, 1500
  TEST_ASSERT_EQUAL(3, w.restarts());
  w.update(epoch(2000, true), true);
  TEST_ASSERT_EQUAL(300, run_no_fix(w, 2001, 400));  // the next outage starts again at five minutes
}

// A cold receiver warns about spoofing and steps its clock while it acquires: after the node told the detector about
// the restart, none of that may raise an event.
void test_a_restarting_receiver_raises_no_event() {
  Detector d{DetectConfig{}};
  Epoch e;
  e.fix_ok = true;
  e.sats_tracked = 12;
  e.cn0_mean = 42;
  e.cn0_spread_x10 = 38;
  e.jam_state = 1;
  e.spoof_state = 1;
  uint32_t t = 0;
  for (; t < 120; t++) {
    e.mono_ms = 100000u + t * 1000u;
    e.utc_s = 1795775405u + t;
    d.update(e);
  }
  d.receiver_restarted();
  for (uint32_t k = 0; k < 25; k++, t++) {  // 25 s of cold-start behaviour: spoofing flag, time step
    e.mono_ms = 100000u + t * 1000u;
    e.utc_s = 1795775405u + t + 40;  // the clock stepped by 40 s
    e.spoof_state = 2;
    e.jam_state = 2;
    Verdict v = d.update(e);
    TEST_ASSERT_EQUAL(kNormal, v.event_class);
  }
}

// The same cold-start behaviour WITHOUT telling the detector is an event: the notice is what prevents it.
void test_without_the_restart_notice_a_cold_receiver_raises_an_event() {
  Detector d{DetectConfig{}};
  Epoch e;
  e.fix_ok = true;
  e.sats_tracked = 12;
  e.cn0_mean = 42;
  e.cn0_spread_x10 = 38;
  e.jam_state = 1;
  e.spoof_state = 1;
  uint32_t t = 0;
  for (; t < 120; t++) {
    e.mono_ms = 100000u + t * 1000u;
    e.utc_s = 1795775405u + t;
    d.update(e);
  }
  bool raised = false;
  for (uint32_t k = 0; k < 25; k++, t++) {
    e.mono_ms = 100000u + t * 1000u;
    e.utc_s = 1795775405u + t + 40;
    e.spoof_state = 2;
    e.jam_state = 2;
    if (d.update(e).event_class != kNormal) raised = true;
  }
  TEST_ASSERT_TRUE(raised);
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_a_clean_band_without_a_fix_restarts_the_receiver_after_five_minutes);
  RUN_TEST(test_it_never_restarts_while_the_band_is_not_clean);
  RUN_TEST(test_it_never_restarts_with_a_fix);
  RUN_TEST(test_a_fix_or_a_noisy_band_restarts_the_timer);
  RUN_TEST(test_restarts_are_spaced_then_slowed);
  RUN_TEST(test_a_fix_resets_the_try_count);
  RUN_TEST(test_a_restarting_receiver_raises_no_event);
  RUN_TEST(test_without_the_restart_notice_a_cold_receiver_raises_an_event);
  return UNITY_END();
}
