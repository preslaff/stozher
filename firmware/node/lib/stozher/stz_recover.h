// Receiver recovery: when the radio environment is clean but the receiver has no fix for minutes, restart its
// GNSS engine. Seen on the T-Beam after a long cover: the NEO-6M did not find satellites again for 13 minutes
// until the board was reset. A restart during real jamming only throws state away, so it never fires while the
// noise evidence says the band is not clean. Pure logic, no hardware access; the caller sends the UBX frame.
#pragma once
#ifndef STZ_NO_PER_SV
#include <stdint.h>

#include "stz_gnss.h"

namespace stz {

struct RecoverConfig {
  uint32_t no_fix_s = 300;       // no fix for this long, with a clean band, before the first restart
  uint32_t retry_s = 600;        // between the first `fast_tries` restarts
  uint8_t fast_tries = 3;
  uint32_t slow_retry_s = 3600;  // afterwards (a node indoors with no sky must not restart all day)
};

enum class RecoverAction : uint8_t { None, Restart };

class ReceiverWatch {
 public:
  explicit ReceiverWatch(const RecoverConfig& cfg) : cfg_(cfg) {}
  // `band_clean`: the detector sees no noise evidence and no jamming event. Call once per examined epoch.
  RecoverAction update(const Epoch& e, bool band_clean);
  uint16_t restarts() const { return total_; }

 private:
  RecoverConfig cfg_;
  bool bad_ = false;
  uint32_t bad_since_ms_ = 0;
  uint32_t last_restart_ms_ = 0;
  uint8_t tries_ = 0;  // in this outage
  uint16_t total_ = 0;
};

}  // namespace stz
#endif
