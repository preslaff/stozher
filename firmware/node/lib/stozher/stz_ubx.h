// u-blox UBX protocol, as far as the node needs it (M10): a streaming frame reader for NAV-PVT, NAV-SAT,
// NAV-STATUS and MON-RF, and the CFG-VALSET boot configuration. No dynamic allocation; the reader holds one
// small payload buffer and summarises NAV-SAT on the fly, so the same code fits an 8-bit board.
// Layouts are checked against frames built by pyubx2 (test/test_ubx), not against hardware yet.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace stz {
namespace ubx {

enum class Kind : uint8_t { None, Pvt, Sat, Status, Rf, Other, BadChecksum };

struct Pvt {
  uint32_t itow_ms = 0;
  uint16_t year = 0;
  uint8_t month = 0, day = 0, hour = 0, min = 0, sec = 0;
  bool time_valid = false;  // validDate and validTime
  uint8_t fix_type = 0;
  bool fix_ok = false;  // gnssFixOk
  uint8_t num_sv = 0;
  int32_t lat_e7 = 0, lon_e7 = 0;
  uint32_t h_acc_mm = 0;
  int32_t g_speed_mm_s = 0;
  int32_t nano_ns = 0;
  uint32_t t_acc_ns = 0;
};

// Over the tracked signals (C/N0 > 0) of one NAV-SAT message.
struct SatSummary {
  uint32_t itow_ms = 0;
  uint8_t tracked = 0, used = 0;
  uint8_t cno_mean = 0;       // dB-Hz, rounded
  uint8_t cno_spread_x10 = 0; // standard deviation in tenths of dB, rounded
};

struct Status {
  uint32_t itow_ms = 0;
  uint8_t spoof_state = 0;  // 0 unknown, 1 none indicated, 2 indicated, 3 multiple indications
};

// Worst case over the RF blocks of a MON-RF message.
struct RfSummary {
  uint8_t jam_state = 0;  // 0 unknown, 1 ok, 2 warning, 3 critical
  uint8_t jam_ind = 0;    // 0..255
  uint16_t agc = 0;       // agcCnt
};

class Reader {
 public:
  static constexpr size_t kMaxPayload = 100;  // enough for NAV-PVT (92); NAV-SAT is summarised, not stored
  static constexpr uint16_t kMaxFrame = 1024; // longer claims are treated as noise

  Kind feed(uint8_t byte);

  const Pvt& pvt() const { return pvt_; }
  const SatSummary& sat() const { return sat_; }
  const Status& status() const { return status_; }
  const RfSummary& rf() const { return rf_; }

 private:
  enum class State : uint8_t { Sync1, Sync2, Class, Id, LenLo, LenHi, Payload, CkA, CkB };
  void begin_frame();
  void on_payload_byte(uint8_t b);
  Kind finish();

  State state_ = State::Sync1;
  uint8_t cls_ = 0, id_ = 0, ck_a_ = 0, ck_b_ = 0, rx_ck_a_ = 0;
  uint16_t len_ = 0, pos_ = 0;
  uint8_t buf_[kMaxPayload];

  // NAV-SAT streaming state
  uint8_t sv_[12];
  uint8_t n_ = 0, used_ = 0;
  uint32_t sum_ = 0, sumsq_ = 0, sat_itow_ = 0;

  Pvt pvt_;
  SatSummary sat_;
  Status status_;
  RfSummary rf_;
};

// Days since 1970-01-01 to seconds: UTC time of a PVT fix (0 if the receiver has no valid time yet).
uint32_t unix_time(const Pvt& p);

// One CFG-VALSET frame (RAM layer) from `keys`/`values`, little-endian values of `sizes` bytes (1, 2 or 4).
struct ConfigItem {
  uint32_t key;
  uint8_t size;
  uint32_t value;
};
size_t build_valset(const ConfigItem* items, size_t n, uint8_t* out, size_t cap);

// The node's boot configuration: UBX only on UART1, 1 Hz, and the four messages the detector needs.
extern const ConfigItem kBootConfig[];
extern const size_t kBootConfigCount;
size_t build_boot_config(uint8_t* out, size_t cap);

}  // namespace ubx
}  // namespace stz
