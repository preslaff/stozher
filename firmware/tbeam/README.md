# STOZHER on the T-Beam (Meshtastic fork, experiment)

Instead of a separate XIAO node plus a stock-Meshtastic T-Beam, run the STOZHER logic inside the T-Beam's own
Meshtastic firmware. Analysis and plan: `docs/firmware.md`, section "T-Beam fork". Status: GPS tap and mesh module flashed and
run on a real T-Beam; the KIM1 option (below) only compiled.

What is here (the Meshtastic checkout itself is not vendored; it is GPL-3.0 and lives in its own git worktree):

- `src/StozherTap.{h,cpp}`: receives every byte of the GPS stream, picks the UBX NAV-SVINFO and MON-HW frames out of it
  with `stz::ubx::Reader` (the same code as the XIAO node) and, once per NAV-SVINFO, assembles one STOZHER `Epoch`
  from them plus the position, time and fix that Meshtastic's TinyGPS++ parsed from NMEA. For now it only logs.
- `patches/0001-stozher-gps-tap.patch`: three small edits to the Meshtastic checkout: the tap call in
  `GPS::whileActive()`, two CFG-MSG commands in the NEO-6 branch that turn on NAV-SVINFO and MON-HW, and a build
  environment `tbeam-stozher`. Everything is behind `-D STOZHER_TAP=1`.
- `patches/0003-stozher-gps-newer-ublox.patch`: the same for newer u-blox receivers, in Meshtastic's own setup branch for
  each: u-blox 7 gets NAV-SVINFO + MON-HW, 8 gets NAV-SAT + MON-HW, 9 gets NAV-SAT + MON-RF (all by CFG-MSG), 10 gets
  NAV-SAT + MON-RF by CFG-VALSET in RAM (set again at every boot). The tap and `stz::ubx::Reader` already read both
  generations of messages, so u-blox 6, 7, 8, 9 and 10 all feed the detector. Only u-blox 6 is checked on hardware.
- `variants/stozher_wroom/`: board `diy-wroom-stozher`, an ESP32-WROOM-32 DevKit + RFM95W (SX1276) + any of those u-blox
  receivers, adapted from Meshtastic's DIY v1: GNSS on GPIO 16/17 (not the strapping pin 12), SX1276 only, BOOT button on
  GPIO 0, no floating battery or power-detect pins. Wiring: SCK 5, MISO 19, MOSI 27, NSS 18, RESET 23, DIO0 26, DIO1 33.
  Built, not yet run on the board.
- **Witness and gateway in one board.** Every board with the STOZHER module broadcasts its records over LoRa, so any
  gateway in range forwards them. The same board can also *be* the gateway for other nodes: plugged into a computer running
  the bridge, other nodes' records pass through to it as with stock Meshtastic. Meshtastic never shows the connected
  client what the node sends itself, so while a client is connected (`service->api_state`) the module also hands each of its
  own records to it (`sendToPhone`, a copy of the broadcast); the API counts a copy that also arrives via another gateway as
  a duplicate. The bridge's ack to the node's own ID is delivered locally (`Router::sendLocal`). No setting to switch: the
  role follows from whether a client is connected. Built for both boards; **not yet checked on hardware**.
- **Store and forward** (`src/StozherStore.{h,cpp}`, `patches/0004-stozher-store.patch`, `docs/mesh.md`): keeps every
  witness record the board hears (and its own) in a LittleFS file `/stozher/store.bin`, hands them to the next bridge that
  connects over serial, Wi-Fi or Ethernet, and deletes them when an ack the bridge sends verifies and covers them. The patch
  registers the module, passes the client's packets to it (`MeshService::handleToRadio`, where the bridge's acks are still
  plain text) and adds `MeshService::toPhoneFree()` for pacing. On by default with the witness module; alone with
  `-D STOZHER_STORE=1`. `variants/stozher_t3/`: `tlora-v2-1-1_6-stozher-gw`, a LilyGO T3 V1.6.1 as a gateway with the
  store (Meshtastic's own T3 board files). Built, **not yet run on a board**.
- `src/StozherKeys.h`: the cloud public key the boards trust for acks (both modules).
- `apply.sh`: links `firmware/node/lib/{stozher,tweetnacl}` into the checkout's `lib/`, copies the tap and the extra
  boards, applies the patches.

```
git -C ../Meshtastic-firmware worktree add ../Meshtastic-firmware-stozher -b stozher   # once
firmware/tbeam/apply.sh ../Meshtastic-firmware-stozher
cd ../Meshtastic-firmware-stozher && pio run -e tbeam-stozher        # or -e diy-wroom-stozher, -e tlora-v2-1-1_6-stozher-gw
```

Building one environment with different `PLATFORMIO_BUILD_FLAGS` makes PlatformIO clean the other environments' builds:
copy an image you want to keep (e.g. to the git-ignored `data/firmware-builds/`). Flashing a board is a separate, deliberate step (back up the flash first, see docs/firmware.md). The stock firmware
binaries in `firmware/node/` restore a board.

## Build options (all `-D` flags, e.g. `PLATFORMIO_BUILD_FLAGS="-DSTOZHER_NODE_ID=4242" pio run -e tbeam-stozher`)

| Flag | Meaning |
|---|---|
| `STOZHER_BUILD` | the git short hash of this repository (`-DSTOZHER_BUILD=$(git rev-parse --short HEAD)`, unquoted); printed at boot with the detector revision (`stz::kDetectorRev`, bumped on every rule or threshold change), so a log says exactly what runs |
| `STOZHER_NODE_ID` | the node id registered in the backend (default 1); the master key is the PUBLIC test key until provisioning exists. After a flash the Meshtastic radio ID may change: re-bind the node (docs/mesh.md, "After reflashing a device") |
| `STOZHER_CLOUD_PUBLIC_HEX` | the cloud public key (64 hex digits, unquoted) the board trusts for acks; default the PUBLIC test key. A kit signs with its own key: take it from that kit's `GET /v1/control/public-key`, or the board ignores its acks. The boot log prints `acks trusted from key 1 <first 8 hex digits>`; compare with `make kit-doctor` |
| `STOZHER_STORE=1` / `STOZHER_NO_STORE` | store and forward on (gateway-only builds) / off on a witness build |
| `STOZHER_STORE_MAX_AGE_S` | stored records older than this go (default 7 days; only when the board knows the time) |
| `STZ_STORE_SLOTS` | stored records (default 1024, 48 bytes each in flash and 8 in RAM) |
| `STOZHER_SELFTEST_AT_S=N` | N seconds after boot, feed a synthetic jamming episode; every record built from it is type `test` |
| `MESHTASTIC_EXCLUDE_BLUETOOTH=1` | Meshtastic's own flag (not ours): leave the Bluetooth stack out. Used on the test board after the stack asserted in `ld_acl.c` and rebooted it (Oct 2026). Image 1.86 MB instead of 2.08 MB. We use no phone app |
| `STOZHER_EPOCH_LOG_S=N` | log the detector's numbers (C/N0, drop, baseline, jamming indicator, class; no position) every N seconds, for tuning the rules on real data |
| `STOZHER_KIM1=1` | add the Kinéis KIM1 on UART2 (satellite path). **Never run on hardware yet.** Off: the pins are not touched |
| `STOZHER_KIM1_RX_PIN` / `_TX_PIN` / `_STATUS_PIN` | defaults 36 / 14 / 25. **Placeholders, not checked against the board** |
| `STOZHER_KIM1_BAND` / `_FREQ` / `_POWER_MW` | `AT+TX` parameters; placeholders `B1`, 300, 500 until the organisers give the real values |

KIM1 wiring notes: 5 V supply of its own with a bulk capacitor (the T-Beam's rail is not enough for a 1.5 W burst) and a
common ground; its UART is 3.3 V TTL like the ESP32, no level shifter. The Serial module of Meshtastic must not be
configured with pins (the module checks and leaves the KIM1 off if it is).
