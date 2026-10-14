#include "stz_recover.h"

#ifndef STZ_NO_PER_SV
namespace stz {

RecoverAction ReceiverWatch::update(const Epoch& e, bool band_clean) {
  if (e.fix_ok || !band_clean) {  // a fix, or a band that is not clean: not this watch's business
    bad_ = false;
    if (e.fix_ok) tries_ = 0;
    return RecoverAction::None;
  }
  if (!bad_) {
    bad_ = true;
    bad_since_ms_ = e.mono_ms;
  }
  if ((uint32_t)(e.mono_ms - bad_since_ms_) < cfg_.no_fix_s * 1000u) return RecoverAction::None;
  if (tries_ > 0) {
    uint32_t gap = (tries_ < cfg_.fast_tries ? cfg_.retry_s : cfg_.slow_retry_s) * 1000u;
    if ((uint32_t)(e.mono_ms - last_restart_ms_) < gap) return RecoverAction::None;
  }
  last_restart_ms_ = e.mono_ms;
  if (tries_ < 255) tries_++;
  total_++;
  return RecoverAction::Restart;
}

}  // namespace stz
#endif
