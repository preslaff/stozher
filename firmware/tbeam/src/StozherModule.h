// STOZHER witness module for the T-Beam (see firmware/tbeam/README.md). Compiled only with -D STOZHER_TAP=1.
//
// Epochs from the GPS tap go through the same node logic as the XIAO build (detector, sequence numbers, `STZ1:` mesh
// uplink with spacing and caps) and acknowledgements arrive as `STZA1:` text packets in process. There is no KIM1 on
// this build: the satellite path is switched off in the library (TxConfig::satellite_enabled = false).
#pragma once
#include "configuration.h"

#ifdef STOZHER_TAP
#include "SinglePortModule.h"
#include "concurrency/OSThread.h"

#include "stz_gnss.h"

class StozherModule : public SinglePortModule, private concurrency::OSThread
{
  public:
    StozherModule();

    bool wantPacket(const meshtastic_MeshPacket *p) override;
    ProcessMessage handleReceived(const meshtastic_MeshPacket &mp) override;

    // From the GPS tap: one finished epoch (about once a second).
    void onEpoch(const stz::Epoch &e);
    // Send one text line to the primary channel as a broadcast; false if it could not be queued.
    bool sendText(const char *text, size_t len);
    // The radio is (about to be) transmitting: the receiver must not be judged for this long.
    void noteRadio(uint32_t durationMs);

  protected:
    int32_t runOnce() override;

  private:
    struct Impl;
    Impl *impl;
};

extern StozherModule *stozherModule;
#endif
