// STOZHER tap on the T-Beam's GPS byte stream (see firmware/tbeam/README.md). Compiled only with -D STOZHER_TAP=1.
#pragma once
#include <stdint.h>

class TinyGPSPlus;

// Called from GPS::whileActive() with every byte read from the receiver. Feeds the UBX reader that picks the
// NAV-SVINFO and MON-HW frames out of the stream (TinyGPS++ ignores them) and, once per NAV-SVINFO, assembles one
// STOZHER epoch from them plus the position, time and fix that TinyGPS++ has parsed from NMEA.
void stozherGpsByte(uint8_t b, TinyGPSPlus &gps);
