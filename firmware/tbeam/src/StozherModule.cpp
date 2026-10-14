#include "StozherModule.h"

#ifdef STOZHER_TAP

#include "Channels.h"
#include "MeshService.h"
#include "NodeDB.h"
#include "mesh/RadioTxHook.h"

#include <Preferences.h>

#include "stz_codec.h"
#include "stz_kim1.h"
#include "stz_mesh.h"
#include "stz_node.h"
#include "stz_tx.h"

// Provisioning is not built yet: the node id and master key are build-time values, and the default is the PUBLIC test
// key of tests/vectors, so test records can never be mistaken for a real node's. Register the node in the backend with
// the id below, kind tbeam, and this T-Beam's Meshtastic id as its mesh transport id.
#ifndef STOZHER_NODE_ID
#define STOZHER_NODE_ID 1
#endif
#ifndef STOZHER_SELFTEST_AT_S
#define STOZHER_SELFTEST_AT_S 0 // >0: that many seconds after boot, feed a synthetic jamming episode (records are type `test`)
#endif

StozherModule *stozherModule = nullptr;

namespace
{
// The public half of the test cloud key (tests/vectors/ack_messages.json).
const char kCloudPublicHex[] = "03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8";

class PrefsSeqStore : public stz::SeqStore
{
  public:
    Result load(uint8_t keyId, uint16_t &next) override
    {
        char k[8];
        name(keyId, k);
        if (!prefs.isKey(k))
            return Result::Missing;
        next = prefs.getUShort(k, 0);
        return Result::Ok;
    }
    bool save(uint8_t keyId, uint16_t next) override
    {
        char k[8];
        name(keyId, k);
        return prefs.putUShort(k, next) == sizeof(uint16_t);
    }
    Preferences prefs;

  private:
    static void name(uint8_t keyId, char *out) { snprintf(out, 8, "seq%u", (unsigned)(keyId & 15)); }
};

} // namespace

struct StozherModule::Impl {
    // `STZ1:` lines out through sendToMesh, `STZA1:` lines in from handleReceived.
    class Port : public stz::mesh::Port
    {
      public:
        explicit Port(StozherModule &m) : m_(m) {}
        size_t write(const uint8_t *d, size_t n) override
        {
            size_t len = n;
            while (len && (d[len - 1] == '\n' || d[len - 1] == '\r'))
                len--;
            return m_.sendText((const char *)d, len) ? n : 0;
        }
        int read() override { return rxPos < rxLen ? rx[rxPos++] : -1; }
        void push(const char *t, size_t n)
        {
            if (rxPos == rxLen)
                rxPos = rxLen = 0;
            if (rxLen + n + 1 > sizeof rx)
                return; // full: the sender repeats acks
            memcpy(rx + rxLen, t, n);
            rxLen += n;
            rx[rxLen++] = '\n';
        }

      private:
        StozherModule &m_;
        char rx[512];
        size_t rxPos = 0, rxLen = 0;
    };

    // Every packet the radio driver transmits, ours or not, is a reason not to judge the receiver.
    class TxHook : public RadioTxHook
    {
      public:
        explicit TxHook(StozherModule &m) : m_(m) {}
        PreTxAction beforeTransmit(RadioInterface *, meshtastic_MeshPacket *p) override
        {
            if (p)
                m_.noteRadio(3000);
            return PRETX_SEND;
        }
        void packetReleased(RadioInterface *, const meshtastic_MeshPacket *) override { m_.noteRadio(0); }

      private:
        StozherModule &m_;
    };

    explicit Impl(StozherModule &m) : port(m), hook(m) {}

    Port port;
    TxHook hook;
    stz::kim1::NullPort nullKim;
    PrefsSeqStore store;
    stz::Keys keys;
    stz::CloudKey cloud[1];
    stz::Tx *tx = nullptr;
    stz::mesh::Link *link = nullptr;
    stz::Node *node = nullptr;
    uint32_t bootMs = 0;
    uint32_t acksSeen = 0;
    uint32_t epochs = 0;
    uint32_t lastStatsMs = 0;
};

StozherModule::StozherModule() : SinglePortModule("stozher", meshtastic_PortNum_TEXT_MESSAGE_APP), OSThread("Stozher")
{
    impl = new Impl(*this);
    impl->bootMs = millis();
    impl->store.prefs.begin("stozher", false);

    uint8_t master[16];
    for (int i = 0; i < 16; i++)
        master[i] = (uint8_t)i; // PUBLIC test key
    stz::TxConfig tc;
    tc.satellite_enabled = false;
    tc.node_id = STOZHER_NODE_ID;
    tc.key_id = 0;
    stz::derive_keys(master, tc.node_id, tc.key_id, impl->keys);
    impl->tx = new stz::Tx(tc, impl->keys, impl->nullKim, impl->store);

    impl->cloud[0].id = 1;
    size_t n = 0;
    stz::from_hex(kCloudPublicHex, strlen(kCloudPublicHex), impl->cloud[0].pub, 32, n);
    impl->link = new stz::mesh::Link(stz::mesh::MeshConfig{}, impl->port, impl->cloud, 1);
    impl->node = new stz::Node(stz::DetectConfig{}, *impl->tx, impl->link);

    LOG_WARN("STOZHER: node id %u with the PUBLIC TEST KEY, mesh uplink only (no KIM1). Not a real node.", (unsigned)tc.node_id);
    stozherModule = this;
}

bool StozherModule::wantPacket(const meshtastic_MeshPacket *p)
{
    return p && p->decoded.portnum == ourPortNum;
}

ProcessMessage StozherModule::handleReceived(const meshtastic_MeshPacket &mp)
{
    // Only acknowledgements addressed to this node count. Whether their signature is good is decided by the library.
    if (mp.to != nodeDB->getNodeNum() || mp.decoded.payload.size < 6 || mp.decoded.payload.size > 200)
        return ProcessMessage::CONTINUE;
    if (memcmp(mp.decoded.payload.bytes, "STZA1:", 6) != 0)
        return ProcessMessage::CONTINUE;
    impl->port.push((const char *)mp.decoded.payload.bytes, mp.decoded.payload.size);
    LOG_INFO("STOZHER: ack text from !%08x queued for verification", (unsigned)mp.from);
    return ProcessMessage::CONTINUE;
}

bool StozherModule::sendText(const char *text, size_t len)
{
    if (len == 0 || len > sizeof(((meshtastic_MeshPacket *)nullptr)->decoded.payload.bytes))
        return false;
    meshtastic_MeshPacket *p = allocDataPacket();
    if (!p)
        return false;
    p->to = NODENUM_BROADCAST;
    p->channel = channels.getPrimaryIndex();
    p->want_ack = false;
    p->decoded.want_response = false;
    p->decoded.payload.size = len;
    memcpy(p->decoded.payload.bytes, text, len);
    service->sendToMesh(p);
    LOG_INFO("STOZHER: sent %u bytes to the mesh (%.5s...)", (unsigned)len, text);
    return true;
}

void StozherModule::noteRadio(uint32_t durationMs)
{
    if (impl && impl->tx)
        impl->tx->note_mesh_tx(millis(), durationMs);
}

void StozherModule::onEpoch(const stz::Epoch &in)
{
    stz::Epoch e = in;
    uint32_t now = millis();
    impl->epochs++;

#if STOZHER_SELFTEST_AT_S > 0
    // A scripted episode so the whole chain can be seen without anyone jamming anything: synthetic epochs, so every
    // record built from them is type `test`, never a live alarm.
    uint32_t up = (now - impl->bootMs) / 1000;
    bool inTest = up >= STOZHER_SELFTEST_AT_S - 5 && up < STOZHER_SELFTEST_AT_S + 50;
    impl->node->set_test_mode(inTest);
    if (up >= STOZHER_SELFTEST_AT_S && up < STOZHER_SELFTEST_AT_S + 12) {
        e.cn0_mean = 8;
        e.sats_tracked = 3;
        e.jam_state = 2;
    }
#endif

    stz::Verdict v = impl->node->on_epoch(e, now);
    if (v.changed && !v.blanked)
        LOG_INFO("STOZHER: class -> %u (rules 0x%02x, confidence %u): record %s", v.event_class, v.rules, v.confidence,
                 impl->node->last_submit() == stz::SubmitResult::Queued ? "queued" : "NOT queued");
}

int32_t StozherModule::runOnce()
{
    uint32_t now = millis();
    impl->node->tick(now);
    if (impl->tx->acks_applied() != impl->acksSeen) {
        impl->acksSeen = impl->tx->acks_applied();
        LOG_INFO("STOZHER: signed acknowledgement verified and applied (%u so far)", (unsigned)impl->acksSeen);
    }
    if ((uint32_t)(now - impl->lastStatsMs) > 60000) {
        impl->lastStatsMs = now;
        LOG_INFO("STOZHER: %u epochs, class %u, mesh queue %u, blanked=%d", (unsigned)impl->epochs, impl->node->current_class(),
                 (unsigned)impl->link->pending(), impl->tx->blanked(now) ? 1 : 0);
    }
    return 200;
}

#endif
