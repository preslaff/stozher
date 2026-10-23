#include "StozherStore.h"

#ifdef STOZHER_STORE

#include "Channels.h"
#include "FSCommon.h"
#include "MeshService.h"
#include "NodeDB.h"
#include "RTC.h"
#include "SPILock.h"
#include "concurrency/LockGuard.h"

#include "StozherKeys.h"
#include "stz_codec.h"
#include "stz_mesh.h"
#include "stz_store.h"

#ifndef ARCH_ESP32
#error "STOZHER_STORE keeps its slots in a LittleFS file opened r+: ESP32 only for now"
#endif
#ifndef STOZHER_STORE_MAX_AGE_S
#define STOZHER_STORE_MAX_AGE_S (7u * 24 * 3600)
#endif

StozherStoreModule *stozherStoreModule = nullptr;

namespace
{
const char kDir[] = "/stozher";
const char kPath[] = "/stozher/store.bin";

// The slots in one fixed-size file, rewritten in place. Every access holds the SPI lock, like the rest of Meshtastic's
// file system use. The file is opened once and kept open; a failed access closes it and the next one reopens it.
class FileBackend : public stz::store::Backend
{
  public:
    bool ready = false;

    void begin()
    {
        concurrency::LockGuard g(spiLock);
        if (!FSCom.exists(kDir))
            FSCom.mkdir(kDir);
        size_t want = STZ_STORE_SLOTS * stz::store::kSlotBytes;
        if (FSCom.exists(kPath)) {
            File f = FSCom.open(kPath, FILE_O_READ);
            bool ok = f && f.size() == want;
            if (f)
                f.close();
            if (!ok)
                FSCom.remove(kPath); // another size (another build): start again
        }
        if (!FSCom.exists(kPath)) {
            File f = FSCom.open(kPath, FILE_O_WRITE);
            if (!f)
                return;
            uint8_t zero[stz::store::kSlotBytes] = {0};
            for (size_t i = 0; i < STZ_STORE_SLOTS; i++)
                if (f.write(zero, sizeof zero) != sizeof zero) {
                    f.close();
                    FSCom.remove(kPath);
                    return;
                }
            f.close();
        }
        ready = true;
    }
    bool read(size_t slot, uint8_t out[stz::store::kSlotBytes]) override
    {
        concurrency::LockGuard g(spiLock);
        if (!open() || !f_.seek(slot * stz::store::kSlotBytes) || f_.read(out, stz::store::kSlotBytes) != stz::store::kSlotBytes)
            return fail();
        return true;
    }
    bool write(size_t slot, const uint8_t in[stz::store::kSlotBytes]) override
    {
        concurrency::LockGuard g(spiLock);
        if (!open() || !f_.seek(slot * stz::store::kSlotBytes) || f_.write(in, stz::store::kSlotBytes) != stz::store::kSlotBytes)
            return fail();
        f_.flush();
        return true;
    }

  private:
    bool open()
    {
        if (f_)
            return true;
        if (!ready)
            return false;
        f_ = FSCom.open(kPath, "r+");
        return (bool)f_;
    }
    bool fail()
    {
        if (f_)
            f_.close();
        return false;
    }
    File f_;
};

} // namespace

struct StozherStoreModule::Impl {
    FileBackend backend;
    stz::store::Store store{backend};
    stz::CloudKey cloud[1];
    bool connected = false;
    bool replaying = false;
    uint32_t cursor = 0;   // last order handed over in this replay
    uint32_t replayTo = 0; // the newest order when the client connected: later records reach it live
    uint32_t replayed = 0, replayedTotal = 0;
    uint32_t lastHourMs = 0;
};

StozherStoreModule::StozherStoreModule() : SinglePortModule("stozherstore", meshtastic_PortNum_TEXT_MESSAGE_APP), OSThread("StozherStore")
{
    impl = new Impl();
    impl->backend.begin();
    impl->store.begin();
    impl->cloud[0].id = kStozherCloudKeyId;
    size_t n = 0;
    stz::from_hex(kStozherCloudPublicHex, strlen(kStozherCloudPublicHex), impl->cloud[0].pub, 32, n);
    if (!impl->backend.ready)
        LOG_ERROR("STOZHER store: no file system space, records are NOT kept for the next bridge");
    STOZHER_LOG_TRUSTED_KEY("store");
    LOG_INFO("STOZHER store: %u records held from %u senders (%u slots)", (unsigned)impl->store.counts().held,
             (unsigned)impl->store.counts().senders, (unsigned)STZ_STORE_SLOTS);
    stozherStoreModule = this;
}

ProcessMessage StozherStoreModule::handleReceived(const meshtastic_MeshPacket &mp)
{
    // Our own broadcasts come here too (the router delivers them locally), so a witness that is also a gateway keeps its own.
    if (mp.which_payload_variant != meshtastic_MeshPacket_decoded_tag || mp.channel != channels.getPrimaryIndex())
        return ProcessMessage::CONTINUE;
    uint8_t raw[stz::kMaxLen];
    size_t len = 0;
    if (!stz::store::parse_text((const char *)mp.decoded.payload.bytes, mp.decoded.payload.size, raw, len))
        return ProcessMessage::CONTINUE;
    uint32_t when = mp.rx_time ? mp.rx_time : getValidTime(RTCQualityFromNet);
    // With a bridge connected, Meshtastic hands it this packet itself (our own ones, the witness module's copy).
    stz::store::AddResult r = impl->store.add(getFrom(&mp), when, raw, len, stozherBridgeConnected());
    if (r == stz::store::AddResult::Stored)
        LOG_DEBUG("STOZHER store: record from !%08x kept (%u held)", (unsigned)getFrom(&mp), (unsigned)impl->store.counts().held);
    else if (r == stz::store::AddResult::NoRoom)
        LOG_WARN("STOZHER store: record from !%08x NOT kept (no room or flash error)", (unsigned)getFrom(&mp));
    return ProcessMessage::CONTINUE;
}

void StozherStoreModule::noteClientPacket(const meshtastic_MeshPacket &p)
{
    if (p.which_payload_variant != meshtastic_MeshPacket_decoded_tag || p.decoded.portnum != meshtastic_PortNum_TEXT_MESSAGE_APP)
        return;
    if (isBroadcast(p.to) || p.decoded.payload.size < 6 || memcmp(p.decoded.payload.bytes, "STZA1:", 6) != 0)
        return;
    int n = impl->store.apply_ack_text(p.to, (const char *)p.decoded.payload.bytes, p.decoded.payload.size, impl->cloud, 1);
    if (n < 0)
        LOG_WARN("STOZHER store: ack to !%08x does not verify under the trusted key: nothing deleted", (unsigned)p.to);
    else
        LOG_INFO("STOZHER store: ack to !%08x verified, %d stored records deleted (%u held)", (unsigned)p.to, n,
                 (unsigned)impl->store.counts().held);
}

int32_t StozherStoreModule::runOnce()
{
    uint32_t now = millis();
    bool connected = stozherBridgeConnected();
    if (connected && !impl->connected) {
        impl->replaying = impl->store.counts().held > 0;
        impl->cursor = 0;
        impl->replayTo = impl->store.last_order();
        impl->replayed = 0;
        if (impl->replaying)
            LOG_INFO("STOZHER store: a bridge connected, handing over %u stored records", (unsigned)impl->store.counts().held);
    }
    if (!connected && impl->replaying)
        LOG_INFO("STOZHER store: the bridge went away after %u of the stored records", (unsigned)impl->replayed);
    if (!connected)
        impl->replaying = false;
    impl->connected = connected;

    // A few per pass, and never more than leaves 4 places in the client queue for live traffic.
    for (int i = 0; impl->replaying && i < 4 && service->toPhoneFree() > 4; i++) {
        stz::store::Entry e;
        if (!impl->store.next(impl->cursor, e) || e.order > impl->replayTo) {
            impl->replaying = false;
            LOG_INFO("STOZHER store: handed over %u stored records (%u since boot)", (unsigned)impl->replayed, (unsigned)impl->replayedTotal);
            break;
        }
        char text[5 + 2 * stz::kMaxLen + 1];
        size_t n = stz::mesh::build_text(e.raw, e.len, text, sizeof text);
        if (n < 2) {
            impl->cursor = e.order; // cannot happen for a stored record (28 or 32 bytes); skip it rather than stall
            continue;
        }
        meshtastic_MeshPacket *p = allocDataPacket();
        if (!p)
            break; // no packet free: the same record again on the next pass
        impl->cursor = e.order;
        n--; // no newline in a packet
        p->from = e.from;
        p->to = NODENUM_BROADCAST;
        p->channel = channels.getPrimaryIndex();
        p->rx_time = e.rx_time;
        p->decoded.payload.size = n;
        memcpy(p->decoded.payload.bytes, text, n);
        service->sendToPhone(p);
        impl->store.mark_handed(e.order);
        impl->replayed++;
        impl->replayedTotal++;
    }

    if ((uint32_t)(now - impl->lastHourMs) > 3600000UL || impl->lastHourMs == 0) {
        impl->lastHourMs = now;
        impl->store.expire(getValidTime(RTCQualityFromNet), STOZHER_STORE_MAX_AGE_S);
        const stz::store::Counts &c = impl->store.counts();
        LOG_INFO("STOZHER store: %u held from %u senders; since boot %u kept, %u repeats, %u deleted by ack, %u pushed out, %u expired",
                 (unsigned)c.held, (unsigned)c.senders, (unsigned)c.stored, (unsigned)c.duplicates, (unsigned)c.deleted_by_ack,
                 (unsigned)c.evicted, (unsigned)c.expired);
    }
    return impl->replaying ? 50 : 200;
}

#endif
