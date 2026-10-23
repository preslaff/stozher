// STOZHER store and forward on a gateway (docs/mesh.md, "Store and forward on the gateway"). Compiled only with
// -D STOZHER_STORE=1 (a gateway with no witness module), and by default with the witness module (STOZHER_TAP).
//
// Every `STZ1:` witness record heard on the primary channel (and this board's own, when it is a witness too) is kept in
// flash. When a bridge connects over serial, Wi-Fi or Ethernet, the stored records are handed to it, oldest first, as
// packets received from their original senders. A stored record is deleted when an ack the connected bridge sends
// verifies under the trusted cloud key and shows the backend has it; otherwise it goes by age or for room.
#pragma once
#include "configuration.h"

// On by default on a witness board (it may be the gateway too); -D STOZHER_NO_STORE leaves it out.
#if defined(STOZHER_TAP) && !defined(STOZHER_STORE) && !defined(STOZHER_NO_STORE)
#define STOZHER_STORE 1
#endif

#ifdef STOZHER_STORE
#include "SinglePortModule.h"
#include "concurrency/OSThread.h"

class StozherStoreModule : public SinglePortModule, private concurrency::OSThread
{
  public:
    StozherStoreModule();

    ProcessMessage handleReceived(const meshtastic_MeshPacket &mp) override;
    // A packet the connected client asked us to send (MeshService::handleToRadio): acks to nodes are read here.
    void noteClientPacket(const meshtastic_MeshPacket &p);

  protected:
    int32_t runOnce() override;

  private:
    struct Impl;
    Impl *impl;
};

extern StozherStoreModule *stozherStoreModule;
#endif

#if defined(STOZHER_STORE) || defined(STOZHER_TAP)
#include "MeshService.h"
// A STOZHER bridge is connected: a client on USB or the network. Not a phone over Bluetooth, which would only see a list of
// hex lines. Used by the store (replay, and records counted as handed live) and by the witness module (its own copies).
inline bool stozherBridgeConnected()
{
    MeshService::APIState s = service->api_state;
    return s == MeshService::STATE_SERIAL || s == MeshService::STATE_WIFI || s == MeshService::STATE_ETH;
}
#endif

#ifdef STOZHER_STORE
inline void stozherStoreNoteClientPacket(const meshtastic_MeshPacket &p)
{
    if (stozherStoreModule)
        stozherStoreModule->noteClientPacket(p);
}
#endif
