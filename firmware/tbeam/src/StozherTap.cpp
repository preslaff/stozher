#include "StozherTap.h"
#include "configuration.h"

#ifdef STOZHER_TAP // a gateway-only build (STOZHER_STORE alone) has no GPS tap

#include "TinyGPS++.h"
#include "StozherModule.h"
#include "stz_gnss.h"
#include "stz_ubx.h"

namespace
{
stz::ubx::Reader ubxReader;
stz::ubx::RfSummary lastRf;
bool haveRf = false;
bool loggedFirstRf = false;
uint32_t counts[7] = {0, 0, 0, 0, 0, 0, 0}; // indexed by stz::ubx::Kind
uint32_t lastCountsLogMs = 0;

// One second of features: the receiver's own view (NAV-SVINFO, MON-HW) and what TinyGPS++ parsed from NMEA.
stz::Epoch makeEpoch(TinyGPSPlus &gps)
{
    stz::Epoch e;
    e.mono_ms = millis();
    const stz::ubx::SatSummary &s = ubxReader.sat();
    e.sats_tracked = s.tracked;
    e.sats_used = s.used;
    e.cn0_mean = s.cno_mean;
    e.cn0_spread_x10 = s.cno_spread_x10;
    if (haveRf) {
        e.jam_state = lastRf.jam_state;
        e.jam_ind = lastRf.jam_ind;
        e.noise = lastRf.noise;
        e.agc = lastRf.agc;
    }
    bool fix = gps.location.isValid() && gps.location.age() < 3000;
    e.fix_ok = fix;
    e.num_sv = (uint8_t)gps.satellites.value();
    if (fix) {
        e.lat_e7 = (int32_t)(gps.location.lat() * 1e7);
        e.lon_e7 = (int32_t)(gps.location.lng() * 1e7);
        e.speed_mm_s = (int32_t)(gps.speed.mps() * 1000.0);
    }
    if (gps.date.isValid() && gps.time.isValid()) {
        stz::ubx::Pvt p;
        p.year = gps.date.year();
        p.month = gps.date.month();
        p.day = gps.date.day();
        p.hour = gps.time.hour();
        p.min = gps.time.minute();
        p.sec = gps.time.second();
        p.time_valid = true;
        e.utc_s = stz::ubx::unix_time(p);
    }
    return e;
}
} // namespace

void stozherGpsByte(uint8_t b, TinyGPSPlus &gps)
{
    using stz::ubx::Kind;
    Kind k = ubxReader.feed(b);
    if (k == Kind::None)
        return;
    counts[(int)k]++;

    if (k == Kind::Rf) {
        lastRf = ubxReader.rf();
        haveRf = true;
        if (!loggedFirstRf) {
            loggedFirstRf = true;
            LOG_INFO("STOZHER first MON-HW: jam_state=%u jam_ind=%u noise=%u agc=%u", lastRf.jam_state, lastRf.jam_ind, lastRf.noise,
                     lastRf.agc);
        }
    } else if (k == Kind::Sat) {
        stz::Epoch e = makeEpoch(gps);
        if (stozherModule)
            stozherModule->onEpoch(e);
        static uint32_t n = 0;
        if (n++ % 10 == 0)
            LOG_INFO("STOZHER epoch tracked=%u used=%u cn0=%u spread_x10=%u jam_ind=%u jam_state=%u fix=%d nmea_sats=%u utc=%u", e.sats_tracked,
                 e.sats_used, e.cn0_mean, e.cn0_spread_x10, e.jam_ind, e.jam_state, e.fix_ok ? 1 : 0, e.num_sv, (unsigned)e.utc_s);
    }

    if ((uint32_t)(millis() - lastCountsLogMs) > 30000) {
        lastCountsLogMs = millis();
        LOG_INFO("STOZHER ubx frames: svinfo=%u hw=%u other=%u bad_checksum=%u", (unsigned)counts[(int)Kind::Sat], (unsigned)counts[(int)Kind::Rf],
                 (unsigned)counts[(int)Kind::Other], (unsigned)counts[(int)Kind::BadChecksum]);
    }
}

#endif
